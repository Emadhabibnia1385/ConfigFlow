# -*- coding: utf-8 -*-
"""
User and admin notification helpers: purchase delivery, admin alerts,
pending-order fulfillment.
"""
import io
import json
import qrcode
import urllib.parse
from telebot import types

from ..config import ADMIN_IDS
from ..db import (
    get_purchase, get_user, get_package, get_conn,
    assign_config_to_user, get_available_configs_for_package,
    fulfill_pending_order, get_waiting_pending_orders_for_package,
    get_pending_order, get_all_admin_users, setting_get,
)
from ..helpers import esc, fmt_price
from ..bot_instance import bot
from ..group_manager import send_to_topic


def _bot_notif_on(key: str) -> bool:
    """Return True if bot notifications for this key are enabled."""
    return setting_get(f"notif_bot_{key}", "1") == "1"


# ── Purchase delivery ──────────────────────────────────────────────────────────
def deliver_purchase_message(chat_id, purchase_id):
    item = get_purchase(purchase_id)
    if not item:
        bot.send_message(chat_id, "❌ اطلاعات خرید یافت نشد.")
        return
    cfg          = item["config_text"]
    service_name = urllib.parse.unquote(item["service_name"] or "")
    expired_note = "\n\n⚠️ <b>این سرویس توسط ادمین منقضی شده است.</b>" if item["is_expired"] else ""
    text = (
        f"✅ <b>{'تست رایگان' if item['is_test'] else 'سرویس شما آماده است'}</b>\n\n"
        f"🔮 نام سرویس: <b>{esc(service_name)}</b>\n"
        f"🧩 نوع سرویس: <b>{esc(item['type_name'])}</b>\n"
        f"🔋 حجم: <b>{item['volume_gb']}</b> گیگ\n"
        f"⏰ مدت: <b>{item['duration_days']}</b> روز\n\n"
        f"💝 <b>Config:</b>\n<code>{esc(cfg)}</code>\n\n"
        f"🔋 Volume web: {esc(item['inquiry_link'] or '-')}"
        f"{expired_note}"
    )
    qr_img = qrcode.make(cfg)
    bio    = io.BytesIO()
    qr_img.save(bio, format="PNG")
    bio.seek(0)
    bio.name = "qrcode.png"

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("♻️ تمدید", callback_data=f"renew:{purchase_id}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    bot.send_photo(chat_id, bio, caption=text, parse_mode="HTML", reply_markup=kb)

    # also mirror to is_test=1 → test_report topic, else → purchase_log topic
    if item.get("is_test"):
        send_to_topic("test_report",
            f"🧪 <b>تست رایگان</b>\n\n"
            f"👤 کاربر: {esc(item.get('full_name', str(chat_id)))}\n"
            f"🧩 نوع: {esc(item['type_name'])}\n"
            f"📦 پکیج: {esc(item['package_name'] if 'package_name' in item.keys() else '')}\n"
            f"🔮 سرویس: {esc(service_name)}"
        )
    type_desc = item["type_description"] if item["type_description"] else ""
    if type_desc:
        bot.send_message(chat_id, f"📌 <b>توضیحات سرویس:</b>\n\n{esc(type_desc)}", parse_mode="HTML")


# ── Admin notifications ────────────────────────────────────────────────────────
def admin_purchase_notify(method_label, user_row, package_row):
    text = (
        f"❗️ | خرید جدید ({method_label})\n\n"
        f"▫️ آیدی کاربر: <code>{user_row['user_id']}</code>\n"
        f"👨‍💼 نام: {esc(user_row['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(user_row['username'] or 'ندارد')}\n"
        f"💰 مبلغ: {fmt_price(package_row['price'])} تومان\n"
        f"🚦 سرور: {esc(package_row['type_name'])}\n"
        f"✏️ پکیج: {esc(package_row['name'])}\n"
        f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
        f"⏰ مدت: {package_row['duration_days']} روز"
    )
    for admin_id in ADMIN_IDS:
        if not _bot_notif_on("purchase_log"): break
        try:
            bot.send_message(admin_id, text)
        except Exception:
            pass
    if _bot_notif_on("purchase_log"):
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            perms = json.loads(row["permissions"] or "{}")
            if not (perms.get("full") or perms.get("approve_payments")):
                continue
            try:
                bot.send_message(sub_id, text)
            except Exception:
                pass
    send_to_topic("purchase_log", text)


def admin_renewal_notify(user_id, purchase_item, package_row, amount, method_label):
    user_row  = get_user(user_id)
    config_id = purchase_item["config_id"]
    text = (
        f"♻️ | <b>درخواست تمدید</b> ({method_label})\n\n"
        f"👤 کاربر: {esc(user_row['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(user_row['username'] or 'ندارد')}\n"
        f"🆔 آیدی: <code>{user_row['user_id']}</code>\n"
        f"💰 مبلغ پرداختی: <b>{fmt_price(amount)}</b> تومان\n\n"
        f"📌 <b>سرویس فعلی:</b>\n"
        f"🔮 نام: {esc(urllib.parse.unquote(purchase_item['service_name'] or ''))}\n"
        f"🧩 نوع: {esc(purchase_item['type_name'])}\n\n"
        f"📦 <b>پکیج تمدید:</b>\n"
        f"✏️ نام: {esc(package_row['name'])}\n"
        f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
        f"⏰ مدت: {package_row['duration_days']} روز"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ تمدید انجام شد",
                                       callback_data=f"renew:confirm:{config_id}:{user_id}"))
    for admin_id in ADMIN_IDS:
        if not _bot_notif_on("renewal_request"): break
        try:
            bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            pass
    if _bot_notif_on("renewal_request"):
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            perms = json.loads(row["permissions"] or "{}")
            if not (perms.get("full") or perms.get("approve_renewal")):
                continue
            try:
                bot.send_message(sub_id, text, reply_markup=kb)
            except Exception:
                pass
    send_to_topic("renewal_request", text, reply_markup=kb)


def notify_pending_order_to_admins(pending_id, user_id, package_row, amount, method):
    user = get_user(user_id)
    text = (
        f"⚠️ <b>سفارش در انتظار کانفیگ</b>\n\n"
        f"👤 کاربر: {esc(user['full_name'])}\n"
        f"🆔 آیدی: <code>{user_id}</code>\n"
        f"💰 مبلغ: {fmt_price(amount)} تومان\n"
        f"💳 روش پرداخت: {method}\n\n"
        f"📦 <b>پکیج:</b>\n"
        f"🧩 نوع: {esc(package_row['type_name'])}\n"
        f"✏️ نام: {esc(package_row['name'])}\n"
        f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
        f"⏰ مدت: {package_row['duration_days']} روز\n"
        f"💰 قیمت: {fmt_price(package_row['price'])} تومان\n\n"
        "⚠️ موجودی تحویل فوری برای این پکیج تمام شده است.\n"
        "لطفاً برای این سفارش یک کانفیگ ثبت کنید:"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📝 ثبت کانفیگ برای این سفارش",
                                       callback_data=f"adm:pending:addcfg:{pending_id}"))
    for admin_id in ADMIN_IDS:
        if not _bot_notif_on("payment_approval"): break
        try:
            bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            pass
    if _bot_notif_on("payment_approval"):
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            perms = json.loads(row["permissions"] or "{}")
            if not (perms.get("full") or perms.get("approve_payments") or perms.get("approve_renewal")):
                continue
            try:
                bot.send_message(sub_id, text, reply_markup=kb)
            except Exception:
                pass
    send_to_topic("payment_approval", text, reply_markup=kb)


# ── Pending order fulfillment ──────────────────────────────────────────────────
def _complete_pending_order(pending_id, cfg_name, cfg_text, inquiry_link):
    """Register a new config, assign it to the pending-order user, deliver it."""
    p_row = get_pending_order(pending_id)
    if not p_row or p_row["status"] == "fulfilled":
        return False
    package_id = p_row["package_id"]
    user_id    = p_row["user_id"]
    pkg        = get_package(package_id)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO configs(service_name, config_text, inquiry_link, package_id, type_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (cfg_name, cfg_text, inquiry_link, package_id, pkg["type_id"] if pkg else None)
        )
        config_id = cur.lastrowid
    purchase_id = assign_config_to_user(
        config_id, user_id, package_id,
        p_row["amount"], p_row["payment_method"], is_test=0
    )
    fulfill_pending_order(pending_id)
    user = get_user(user_id)
    try:
        bot.send_message(
            user_id,
            "🎉 <b>کانفیگ شما آماده شد!</b>\n\n"
            "سفارش شما توسط پشتیبانی تکمیل شد. جزئیات سرویس در ادامه ارسال می‌شود."
        )
    except Exception:
        pass
    deliver_purchase_message(user_id, purchase_id)
    if pkg:
        admin_purchase_notify(p_row["payment_method"], user, pkg)
    return True


def auto_fulfill_pending_orders(package_id):
    """After new configs are added for a package, automatically fill waiting orders."""
    pending_list    = get_waiting_pending_orders_for_package(package_id)
    fulfilled_count = 0
    for p_row in pending_list:
        available = get_available_configs_for_package(package_id)
        if not available:
            break
        cfg        = available[0]
        user_id    = p_row["user_id"]
        pending_id = p_row["id"]
        try:
            purchase_id = assign_config_to_user(
                cfg["id"], user_id, package_id,
                p_row["amount"], p_row["payment_method"], is_test=0
            )
            fulfill_pending_order(pending_id)
        except Exception as e:
            for admin_id in ADMIN_IDS:
                try:
                    bot.send_message(
                        admin_id,
                        f"⚠️ خطا در تحویل سفارش #{pending_id} به کاربر {user_id}:\n<code>{e}</code>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            continue
        try:
            bot.send_message(
                user_id,
                "🎉 <b>کانفیگ شما آماده شد!</b>\n\n"
                "سفارش شما تکمیل شد. جزئیات سرویس در ادامه ارسال می‌شود.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        try:
            deliver_purchase_message(user_id, purchase_id)
        except Exception:
            pass
        try:
            pkg  = get_package(package_id)
            user = get_user(user_id)
            if pkg and user:
                admin_purchase_notify(p_row["payment_method"], user, pkg)
        except Exception:
            pass
        fulfilled_count += 1
    return fulfilled_count
