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
    count_referrals, get_unrewarded_start_referrals,
    get_unrewarded_start_referrals_channel,
    mark_start_reward_given, get_unrewarded_purchase_referees,
    mark_purchase_reward_given, get_referral_by_referee,
    update_balance, mark_referee_channel_joined,
)
from ..helpers import esc, fmt_price
from ..bot_instance import bot
from ..group_manager import send_to_topic


def _bot_notif_on(key: str) -> bool:
    """Return True if bot (sub-admin) notifications for this key are enabled."""
    return setting_get(f"notif_bot_{key}", "1") == "1"


def _own_notif_on(key: str) -> bool:
    """Return True if owner (ADMIN_IDS) notifications for this key are enabled."""
    return setting_get(f"notif_own_{key}", "1") == "1"


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
    if setting_get("manual_renewal_enabled", "1") == "1":
        kb.add(types.InlineKeyboardButton("♻️ تمدید", callback_data=f"renew:{purchase_id}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    bot.send_photo(chat_id, bio, caption=text, parse_mode="HTML", reply_markup=kb)

    # also mirror to is_test=1 → test_report topic, else → purchase_log topic
    if item["is_test"]:
        send_to_topic("test_report",
            f"🧪 <b>تست رایگان</b>\n\n"
            f"👤 کاربر: <code>{chat_id}</code>\n"
            f"🧩 نوع: {esc(item['type_name'])}\n"
            f"📦 پکیج: {esc(item['package_name'])}\n"
            f"🔮 سرویس: {esc(service_name)}"
        )
    type_desc = item["type_description"] if item["type_description"] else ""
    if type_desc:
        bot.send_message(chat_id, f"📌 <b>توضیحات سرویس:</b>\n\n{esc(type_desc)}", parse_mode="HTML")

    # Check referral purchase reward (only for non-test purchases)
    if not item["is_test"]:
        try:
            check_and_give_referral_purchase_reward(chat_id)
        except Exception:
            pass
        try:
            notify_referral_first_purchase(chat_id)
        except Exception:
            pass


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
    if _own_notif_on("purchase_log"):
        for admin_id in ADMIN_IDS:
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
    # If the buyer is an agent, also mirror to agency_log
    if user_row["is_agent"]:
        send_to_topic("agency_log", text)


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
    if _own_notif_on("renewal_request"):
        for admin_id in ADMIN_IDS:
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
    # If the user is an agent, also mirror to agency_log
    if user_row and user_row["is_agent"]:
        send_to_topic("agency_log", text, reply_markup=kb)


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
    if _own_notif_on("payment_approval"):
        for admin_id in ADMIN_IDS:
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


# ── Referral Reward Logic ──────────────────────────────────────────────────────
def _give_referral_reward(referrer_id, reward_prefix):
    """Give a referral reward (wallet charge or config) to referrer_id.
    reward_prefix: 'referral_start_reward' or 'referral_purchase_reward'
    This function should only be called ONCE per eligible batch - callers are responsible
    for idempotency (marking reward_given before calling this).
    """
    reward_type = setting_get(f"{reward_prefix}_type", "wallet")
    if reward_type == "wallet":
        amount = int(setting_get(f"{reward_prefix}_amount", "0"))
        if amount > 0:
            update_balance(referrer_id, amount)
            msg = (
                f"🎁 <b>هدیه زیرمجموعه‌گیری!</b>\n\n"
                f"✅ تبریک! زیرمجموعه‌ات شرایط رو کامل کرد.\n"
                f"💰 مبلغ <b>{fmt_price(amount)}</b> تومان به کیف پول شما اضافه شد. 🎉"
            )
            try:
                bot.send_message(referrer_id, msg, parse_mode="HTML")
            except Exception:
                pass
            send_to_topic("wallet_log",
                f"🎁 <b>هدیه زیرمجموعه‌گیری</b>\n\n"
                f"👤 دعوت‌کننده: <code>{referrer_id}</code>\n"
                f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان\n"
                f"📌 نوع: {'ریوارد عضویت' if reward_prefix == 'referral_start_reward' else 'ریوارد خرید'}"
            )
    else:
        # Config reward
        pkg_id = setting_get(f"{reward_prefix}_package", "")
        if not pkg_id or not pkg_id.isdigit():
            return
        pkg = get_package(int(pkg_id))
        if not pkg:
            return
        available = get_available_configs_for_package(int(pkg_id))
        if not available:
            try:
                bot.send_message(
                    referrer_id,
                    "🎁 <b>هدیه زیرمجموعه‌گیری!</b>\n\n"
                    "✅ زیرمجموعه‌ات شرایط رو کامل کرد!\n"
                    "⚠️ متأسفانه موجودی کانفیگ هدیه تمام شده. "
                    "لطفاً به پشتیبانی اطلاع دهید.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return
        cfg = available[0]
        try:
            purchase_id = assign_config_to_user(
                cfg["id"], referrer_id, int(pkg_id), 0, "referral_gift", is_test=0
            )
            bot.send_message(
                referrer_id,
                "🎁 <b>هدیه زیرمجموعه‌گیری!</b>\n\n"
                "✅ زیرمجموعه‌ات شرایط رو کامل کرد!\n"
                "یک کانفیگ رایگان به شما تعلق گرفت! 🎉\n"
                "جزئیات سرویس در ادامه ارسال می‌شود.",
                parse_mode="HTML"
            )
            deliver_purchase_message(referrer_id, purchase_id)
        except Exception:
            pass


def notify_referral_join(referrer_id, referee_id):
    """Send a join-referral log to admins (own/bot) and the referral_log topic."""
    referrer = get_user(referrer_id)
    referee  = get_user(referee_id)
    if not referrer or not referee:
        return
    total = count_referrals(referrer_id)
    text = (
        f"🔗 <b>زیرمجموعه‌گیری جدید</b>\n\n"
        f"👤 <b>دعوت‌کننده:</b>\n"
        f"▫️ نام: {esc(referrer['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(referrer['username'] or 'ندارد')}\n"
        f"🆔 آیدی: <code>{referrer_id}</code>\n"
        f"👥 کل زیرمجموعه‌ها: <b>{total}</b>\n\n"
        f"🆕 <b>کاربر جدید (زیرمجموعه):</b>\n"
        f"▫️ نام: {esc(referee['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(referee['username'] or 'ندارد')}\n"
        f"🆔 آیدی: <code>{referee_id}</code>"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👤 دعوت‌کننده", url=f"tg://user?id={referrer_id}"),
        types.InlineKeyboardButton("🆕 زیرمجموعه",  url=f"tg://user?id={referee_id}"),
    )
    if _own_notif_on("referral_log"):
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
    if _bot_notif_on("referral_log"):
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            try:
                bot.send_message(sub_id, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
    send_to_topic("referral_log", text, reply_markup=kb)


def notify_referral_first_purchase(referee_id):
    """Called after a purchase. If buyer was referred, log the event to referral_log."""
    ref = get_referral_by_referee(referee_id)
    if not ref:
        return
    referrer_id = ref["referrer_id"]
    referrer = get_user(referrer_id)
    referee  = get_user(referee_id)
    if not referrer or not referee:
        return
    text = (
        f"🛍 <b>اولین خرید زیرمجموعه</b>\n\n"
        f"👤 <b>دعوت‌کننده:</b>\n"
        f"▫️ نام: {esc(referrer['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(referrer['username'] or 'ندارد')}\n"
        f"🆔 آیدی: <code>{referrer_id}</code>\n\n"
        f"🛒 <b>خریدار (زیرمجموعه):</b>\n"
        f"▫️ نام: {esc(referee['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(referee['username'] or 'ندارد')}\n"
        f"🆔 آیدی: <code>{referee_id}</code>"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👤 دعوت‌کننده", url=f"tg://user?id={referrer_id}"),
        types.InlineKeyboardButton("🛒 خریدار",      url=f"tg://user?id={referee_id}"),
    )
    if _own_notif_on("referral_log"):
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
    if _bot_notif_on("referral_log"):
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            try:
                bot.send_message(sub_id, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
    send_to_topic("referral_log", text, reply_markup=kb)


def check_and_give_referral_start_reward(referrer_id):
    """
    Called ONLY in 'invite_only' mode (after invited_user starts the bot).
    Checks if referrer qualifies for start reward and gives it once.
    """
    if setting_get("referral_start_reward_enabled", "0") != "1":
        return
    reward_mode = setting_get("referral_start_reward_mode", "invite_only")
    if reward_mode != "invite_only":
        # In channel_join mode, reward is given after channel join - do NOT give here
        return
    required_count = int(setting_get("referral_start_reward_count", "1"))
    unrewarded = get_unrewarded_start_referrals(referrer_id)
    if len(unrewarded) >= required_count:
        batch = [r["referee_id"] for r in unrewarded[:required_count]]
        mark_start_reward_given(referrer_id, batch)
        _give_referral_reward(referrer_id, "referral_start_reward")


def check_and_give_referral_start_reward_after_channel_join(referee_id):
    """
    Called after invited_user (referee) joins the required channel (channel_join mode only).
    - Marks channel_joined in DB (idempotent via channel_joined flag)
    - Fires the deferred notify_referral_join log
    - Gives start reward to referrer if enabled and threshold is reached
    """
    # Only applies to channel_join mode
    reward_mode = setting_get("referral_start_reward_mode", "invite_only")
    if reward_mode != "channel_join":
        return

    ref = get_referral_by_referee(referee_id)
    if not ref:
        return

    # Idempotent: if already processed (channel_joined), skip entirely
    if ref["channel_joined"]:
        return

    # Mark channel joined — this is the single activation point
    mark_referee_channel_joined(referee_id)
    referrer_id = ref["referrer_id"]

    # Fire the deferred join log (was intentionally NOT sent in start.py for this mode)
    try:
        notify_referral_join(referrer_id, referee_id)
    except Exception:
        pass

    # Give start reward only if enabled
    if setting_get("referral_start_reward_enabled", "0") != "1":
        return
    if ref["start_reward_given"]:
        return
    required_count = int(setting_get("referral_start_reward_count", "1"))
    # Get all eligible (channel_joined=1, start_reward_given=0) referees for this referrer
    unrewarded = get_unrewarded_start_referrals_channel(referrer_id)
    if len(unrewarded) >= required_count:
        batch = [r["referee_id"] for r in unrewarded[:required_count]]
        mark_start_reward_given(referrer_id, batch)
        _give_referral_reward(referrer_id, "referral_start_reward")


def check_and_give_referral_purchase_reward(buyer_user_id):
    """Called after a purchase. Check if buyer was referred and give purchase reward to referrer."""
    if setting_get("referral_purchase_reward_enabled", "0") != "1":
        return
    ref = get_referral_by_referee(buyer_user_id)
    if not ref:
        return
    referrer_id = ref["referrer_id"]
    required_count = int(setting_get("referral_purchase_reward_count", "1"))
    unrewarded = get_unrewarded_purchase_referees(referrer_id)
    if len(unrewarded) >= required_count:
        batch = [r["referee_id"] for r in unrewarded[:required_count]]
        mark_purchase_reward_given(referrer_id, batch)
        _give_referral_reward(referrer_id, "referral_purchase_reward")
