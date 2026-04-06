# -*- coding: utf-8 -*-
"""
Payment logic: pricing, gateway selection UI, payment-to-admins dispatch,
card payment approval and rejection.
"""
from telebot import types

from .config import ADMIN_IDS, CRYPTO_COINS, CRYPTO_API_SYMBOLS
from .db import (
    get_user, get_payment, get_package, get_agency_price,
    approve_payment, reject_payment, complete_payment,
    update_balance, reserve_first_config, release_reserved_config,
    assign_config_to_user, get_conn, create_pending_order, get_purchase,
)
from .helpers import esc, fmt_price, display_username, back_button
from .gateways.base import is_gateway_available, is_card_info_complete
from .gateways.crypto import fetch_crypto_prices
from .bot_instance import bot
from .ui.helpers import send_or_edit


# ── Pricing ────────────────────────────────────────────────────────────────────
def get_effective_price(user_id, package_row):
    """Return agency price if user is agent and has a custom price, else regular price."""
    user = get_user(user_id)
    if user and user["is_agent"]:
        ap = get_agency_price(user_id, package_row["id"])
        if ap is not None:
            return ap
    return package_row["price"]


# ── Payment method selection ───────────────────────────────────────────────────
def show_payment_method_selection(target, uid, context_data):
    """
    context_data must contain:
      'kind': 'wallet_charge' or 'config_purchase'
      'amount': int
    """
    amount = context_data["amount"]

    kb = types.InlineKeyboardMarkup()
    if is_gateway_available("card", uid) and is_card_info_complete():
        kb.add(types.InlineKeyboardButton("💳 کارت به کارت", callback_data="pm:card"))
    if is_gateway_available("crypto", uid):
        kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال", callback_data="pm:crypto"))
    if is_gateway_available("tetrapay", uid):
        kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data="pm:tetrapay"))
    if is_gateway_available("swapwallet", uid):
        kb.add(types.InlineKeyboardButton("💎 پرداخت با سواپ ولت", callback_data="pm:swapwallet"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))

    user       = get_user(uid)
    agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""
    send_or_edit(
        target,
        f"💳 <b>انتخاب روش پرداخت</b>\n\n"
        f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان{agent_note}\n\n"
        "روش پرداخت را انتخاب کنید:",
        kb
    )


# ── Crypto UI ──────────────────────────────────────────────────────────────────
def show_crypto_selection(target, amount=None):
    from .db import setting_get
    kb     = types.InlineKeyboardMarkup()
    prices = fetch_crypto_prices() if amount else {}
    has_any = False
    for coin_key, coin_label in CRYPTO_COINS:
        addr = setting_get(f"crypto_{coin_key}", "")
        if addr:
            has_any = True
            symbol     = CRYPTO_API_SYMBOLS.get(coin_key, "")
            price_note = ""
            if amount and symbol and symbol in prices and prices[symbol] > 0:
                coin_amount = amount / prices[symbol]
                price_note  = f" | ≈ {coin_amount:.4f} {symbol}"
            kb.add(types.InlineKeyboardButton(f"{coin_label}{price_note}", callback_data=f"pm:crypto:{coin_key}"))
    if not has_any:
        send_or_edit(target, "⚠️ هیچ آدرس ارز دیجیتالی توسط ادمین ثبت نشده است.", back_button("main"))
        return
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="pm:back"))
    send_or_edit(target, "💎 <b>ارز دیجیتال</b>\n\nنوع ارز مورد نظر را انتخاب کنید:", kb)


def show_crypto_payment_info(target, uid, coin_key, amount):
    from .db import setting_get
    addr   = setting_get(f"crypto_{coin_key}", "")
    label  = next((l for k, l in CRYPTO_COINS if k == coin_key), coin_key)
    symbol = CRYPTO_API_SYMBOLS.get(coin_key, "")
    if not addr:
        send_or_edit(target, "⚠️ آدرس این ارز هنوز توسط ادمین ثبت نشده است.", back_button("main"))
        return
    price_text = ""
    prices = fetch_crypto_prices()
    if symbol and symbol in prices and prices[symbol] > 0:
        coin_amount = amount / prices[symbol]
        price_text  = (
            f"\n\n💱 <b>معادل ارزی:</b> <code>{coin_amount:.6f} {symbol}</code>\n"
            f"برای پرداخت با این ارز باید معادل <b>{coin_amount:.6f} {symbol}</b> واریز نمایید."
        )
    text = (
        f"💎 <b>پرداخت با {label}</b>\n\n"
        f"مبلغ: <b>{fmt_price(amount)}</b> تومان{price_text}\n\n"
        f"📋 آدرس ولت:\n<code>{esc(addr)}</code>\n\n"
        "پس از واریز، تصویر تراکنش یا هش آن را ارسال کنید."
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))

    if hasattr(target, "message"):
        chat_id = target.message.chat.id
        msg_id  = target.message.message_id
        # Always use explicit parse_mode so HTML tags are parsed correctly.
        # send_or_edit omits parse_mode which causes Telegram to raise
        # "can't parse entities" and silently swallow the error, leaving
        # the coin-selection screen unchanged.
        try:
            bot.edit_message_text(
                text, chat_id, msg_id,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass
        # Edit failed: remove buttons so old coins aren't clickable, then
        # send the payment info as a fresh message.
        try:
            bot.delete_message(chat_id, msg_id)
        except Exception:
            try:
                bot.edit_message_reply_markup(
                    chat_id, msg_id,
                    reply_markup=types.InlineKeyboardMarkup()
                )
            except Exception:
                pass
        bot.send_message(chat_id, text, reply_markup=kb,
                         parse_mode="HTML", disable_web_page_preview=True)
    elif hasattr(target, "chat"):
        bot.send_message(target.chat.id, text, reply_markup=kb,
                         parse_mode="HTML", disable_web_page_preview=True)
    else:
        send_or_edit(target, text, kb)


# ── Send payment receipt to admins ─────────────────────────────────────────────
def send_payment_to_admins(payment_id):
    payment     = get_payment(payment_id)
    user        = get_user(payment["user_id"])
    package_row = get_package(payment["package_id"]) if payment["package_id"] else None
    kind_label  = "شارژ کیف پول" if payment["kind"] == "wallet_charge" else "خرید کانفیگ"
    method_label = payment["payment_method"]
    if payment["crypto_coin"]:
        method_label += f" ({payment['crypto_coin']})"
    package_text = ""
    if package_row:
        package_text = (
            f"\n🧩 نوع: {esc(package_row['type_name'])}"
            f"\n📦 پکیج: {esc(package_row['name'])}"
            f"\n🔋 حجم: {package_row['volume_gb']} گیگ"
            f"\n⏰ مدت: {package_row['duration_days']} روز"
        )
    text = (
        f"📥 <b>درخواست جدید برای بررسی</b>\n\n"
        f"🧾 نوع: {kind_label} | {method_label}\n"
        f"👤 کاربر: {esc(user['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
        f"🔢 آیدی: <code>{user['user_id']}</code>\n"
        f"💰 مبلغ: <b>{fmt_price(payment['amount'])}</b> تومان"
        f"{package_text}\n\n"
        f"📝 توضیح کاربر:\n{esc(payment['receipt_text'] or '-')}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ تأیید", callback_data=f"adm:pay:ap:{payment_id}"),
        types.InlineKeyboardButton("❌ رد",    callback_data=f"adm:pay:rj:{payment_id}"),
    )
    for admin_id in ADMIN_IDS:
        try:
            if payment["receipt_file_id"]:
                bot.send_photo(admin_id, payment["receipt_file_id"], caption=text, reply_markup=kb)
            else:
                bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            pass


# ── Card payment approval / rejection ─────────────────────────────────────────
def finish_card_payment_approval(payment_id, admin_note, approved):
    from .ui.notifications import (
        deliver_purchase_message, admin_purchase_notify,
        admin_renewal_notify, notify_pending_order_to_admins,
    )
    payment = get_payment(payment_id)
    if not payment or payment["status"] not in ("pending", "approved", "rejected"):
        return False
    user_id = payment["user_id"]
    if approved:
        approve_payment(payment_id, admin_note)
        if payment["kind"] == "wallet_charge":
            update_balance(user_id, payment["amount"])
            complete_payment(payment_id)
            bot.send_message(user_id, f"✅ واریزی شما تأیید شد.\n\n{esc(admin_note)}")

        elif payment["kind"] == "config_purchase":
            config_id   = payment["config_id"]
            package_id  = payment["package_id"]
            package_row = get_package(package_id)
            if not config_id:
                config_id = reserve_first_config(package_id, payment_id)
            if not config_id:
                pending_id = create_pending_order(
                    user_id, package_id, payment_id, payment["amount"], payment["payment_method"]
                )
                complete_payment(payment_id)
                bot.send_message(
                    user_id,
                    "✅ پرداخت شما تأیید شد.\n\n"
                    "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                    "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                    "🙏 از صبر شما متشکریم."
                )
                notify_pending_order_to_admins(
                    pending_id, user_id,
                    package_row if package_row else {
                        "type_name": "-", "name": "-",
                        "volume_gb": "-", "duration_days": "-", "price": payment["amount"]
                    },
                    payment["amount"], payment["payment_method"]
                )
                return True
            if payment["config_id"] != config_id:
                with get_conn() as conn:
                    conn.execute("UPDATE payments SET config_id=? WHERE id=?", (config_id, payment_id))
            purchase_id = assign_config_to_user(
                config_id, user_id, package_id, payment["amount"],
                payment["payment_method"], is_test=0
            )
            complete_payment(payment_id)
            bot.send_message(user_id, f"✅ واریزی شما تأیید شد.\n\n{esc(admin_note)}")
            deliver_purchase_message(user_id, purchase_id)
            admin_purchase_notify(payment["payment_method"], get_user(user_id), package_row)

        elif payment["kind"] == "renewal":
            package_id  = payment["package_id"]
            package_row = get_package(package_id)
            config_id   = payment["config_id"]
            complete_payment(payment_id)
            bot.send_message(
                user_id,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم."
            )
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT purchase_id FROM configs WHERE id=?", (config_id,)
                ).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item        = get_purchase(purchase_id) if purchase_id else None
            if item and package_row:
                admin_renewal_notify(user_id, item, package_row, payment["amount"], payment["payment_method"])
        return True
    else:
        reject_payment(payment_id, admin_note)
        if payment["config_id"]:
            release_reserved_config(payment["config_id"])
        bot.send_message(user_id, f"❌ رسید شما رد شد.\n\n{esc(admin_note)}")
        return True
