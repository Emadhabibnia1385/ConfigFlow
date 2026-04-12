# -*- coding: utf-8 -*-
"""
Payment logic: pricing, gateway selection UI, payment-to-admins dispatch,
card payment approval and rejection.
"""
from telebot import types
import json

from .config import ADMIN_IDS, CRYPTO_COINS, CRYPTO_API_SYMBOLS
from .db import (
    get_user, get_payment, get_package, get_agency_price,
    get_agency_price_config, get_agency_type_discount,
    approve_payment, reject_payment, complete_payment,
    update_balance, reserve_first_config, release_reserved_config,
    assign_config_to_user, get_conn, create_pending_order, get_purchase,
    get_all_admin_users,
    save_payment_admin_message, get_payment_admin_messages, delete_payment_admin_messages,
)
from .helpers import esc, fmt_price, display_username, back_button
import time
from .gateways.base import is_gateway_available, is_card_info_complete, get_gateway_range_text, is_gateway_in_range, build_gateway_range_guide
from .gateways.crypto import fetch_crypto_prices
from .bot_instance import bot
from .ui.helpers import send_or_edit
from .group_manager import send_to_topic, send_photo_to_topic

# ── Price cache (60 s TTL) — both selection and payment info share the same data
_PRICES_CACHE: dict = {}
_PRICES_CACHE_TS: float = 0.0


def _get_prices() -> dict:
    global _PRICES_CACHE, _PRICES_CACHE_TS
    if time.time() - _PRICES_CACHE_TS < 60 and _PRICES_CACHE:
        return _PRICES_CACHE
    data = fetch_crypto_prices()
    if data:
        _PRICES_CACHE    = data
        _PRICES_CACHE_TS = time.time()
    return _PRICES_CACHE


# ── Pricing ────────────────────────────────────────────────────────────────────
def get_effective_price(user_id, package_row):
    """Return discounted price for agents, else regular price."""
    user = get_user(user_id)
    if not user or not user["is_agent"]:
        return package_row["price"]
    base  = package_row["price"]
    cfg   = get_agency_price_config(user_id)
    mode  = cfg["price_mode"]
    if mode == "global":
        g_type = cfg["global_type"]
        g_val  = cfg["global_val"]
        if g_type == "pct":
            return max(0, base - round(base * g_val / 100))
        else:
            return max(0, base - g_val)
    elif mode == "type":
        type_id = package_row["type_id"]
        td = get_agency_type_discount(user_id, type_id)
        if td:
            if td["discount_type"] == "pct":
                return max(0, base - round(base * td["discount_value"] / 100))
            else:
                return max(0, base - td["discount_value"])
        return base
    else:  # package (default)
        ap = get_agency_price(user_id, package_row["id"])
        return ap if ap is not None else base


# ── Payment method selection ───────────────────────────────────────────────────
def show_payment_method_selection(target, uid, context_data):
    """
    context_data must contain:
      'kind': 'wallet_charge' or 'config_purchase'
      'amount': int
    """
    amount = context_data["amount"]

    _gw_labels = []
    kb = types.InlineKeyboardMarkup()
    from .db import setting_get as _sg
    if is_gateway_available("card", uid) and is_card_info_complete():
        _lbl = _sg("gw_card_display_name", "").strip() or "💳 کارت به کارت"
        kb.add(types.InlineKeyboardButton(_lbl, callback_data="pm:card"))
        _gw_labels.append(("card", _lbl))
    if is_gateway_available("crypto", uid):
        _lbl = _sg("gw_crypto_display_name", "").strip() or "💎 ارز دیجیتال"
        kb.add(types.InlineKeyboardButton(_lbl, callback_data="pm:crypto"))
        _gw_labels.append(("crypto", _lbl))
    if is_gateway_available("tetrapay", uid):
        _lbl = _sg("gw_tetrapay_display_name", "").strip() or "💳 درگاه کارت به کارت (TetraPay)"
        kb.add(types.InlineKeyboardButton(_lbl, callback_data="pm:tetrapay"))
        _gw_labels.append(("tetrapay", _lbl))
    if is_gateway_available("swapwallet_crypto", uid):
        _lbl = _sg("gw_swapwallet_crypto_display_name", "").strip() or "💳 درگاه کارت به کارت و ارز دیجیتال (SwapWallet)"
        kb.add(types.InlineKeyboardButton(_lbl, callback_data="pm:swapwallet_crypto"))
        _gw_labels.append(("swapwallet_crypto", _lbl))
    if is_gateway_available("tronpays_rial", uid):
        _lbl = _sg("gw_tronpays_rial_display_name", "").strip() or "💳 درگاه کارت به کارت (TronsPay)"
        kb.add(types.InlineKeyboardButton(_lbl, callback_data="pm:tronpays_rial"))
        _gw_labels.append(("tronpays_rial", _lbl))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))

    user       = get_user(uid)
    agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""
    _range_guide = build_gateway_range_guide(_gw_labels)
    send_or_edit(
        target,
        f"💳 <b>انتخاب روش پرداخت</b>\n\n"
        f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان{agent_note}\n\n"
        + (_range_guide + "\n\n" if _range_guide else "")
        + "روش پرداخت را انتخاب کنید:",
        kb
    )


# ── Crypto UI ──────────────────────────────────────────────────────────────────
def show_crypto_selection(target, amount=None):
    from .db import setting_get
    kb     = types.InlineKeyboardMarkup()
    prices = _get_prices() if amount else {}
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
    prices = _get_prices()
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
    coin_key = payment["crypto_coin"]
    if coin_key:
        method_label += f" ({coin_key})"
    package_text = ""
    if package_row:
        package_text = (
            f"\n🧩 نوع: {esc(package_row['type_name'])}"
            f"\n📦 پکیج: {esc(package_row['name'])}"
            f"\n🔋 حجم: {package_row['volume_gb']} گیگ"
            f"\n⏰ مدت: {package_row['duration_days']} روز"
        )
    # Crypto equivalent line (shown only for crypto payments)
    crypto_line = ""
    if coin_key:
        symbol = CRYPTO_API_SYMBOLS.get(coin_key, "")
        if symbol:
            prices = _get_prices()
            if symbol in prices and prices[symbol] > 0:
                coin_amount = payment["amount"] / prices[symbol]
                crypto_line = f"\n💱 معادل ارزی: <code>{coin_amount:.6f} {symbol}</code>"
    text = (
        f"📥 <b>درخواست جدید برای بررسی</b>\n\n"
        f"🧾 نوع: {kind_label} | {method_label}\n"
        f"👤 کاربر: {esc(user['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
        f"🔢 آیدی: <code>{user['user_id']}</code>\n"
        f"💰 مبلغ: <b>{fmt_price(payment['amount'])}</b> تومان"
        f"{crypto_line}"
        f"{package_text}\n\n"
        f"📝 توضیح کاربر:\n{esc(payment['receipt_text'] or '-')}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ تأیید", callback_data=f"adm:pay:ap:{payment_id}"),
        types.InlineKeyboardButton("❌ رد",    callback_data=f"adm:pay:rj:{payment_id}"),
    )

    file_id = payment["receipt_file_id"]

    def _send_to_one(target_id):
        """Send payment notification to a single admin/sub-admin.
        
        Strategy:
        - If there is a receipt photo/document: send it FIRST, then the text
          with approve/reject buttons (so admin sees photo before the approval request).
        - If no media: send the text+buttons as a single message.
        
        Returns the tracked Message (the one that holds the approve/reject buttons).
        """
        try:
            # If there's a receipt image/document, send it FIRST (photo before approval text)
            if file_id:
                try:
                    bot.send_photo(target_id, file_id,
                                   caption="🖼 رسید کاربر",
                                   parse_mode="HTML")
                except Exception:
                    try:
                        bot.send_document(target_id, file_id,
                                          caption="📎 رسید کاربر",
                                          parse_mode="HTML")
                    except Exception:
                        pass  # Media forward failure is non-critical
            # Then send the full info text with the approve/reject buttons
            tracked_msg = bot.send_message(
                target_id, text, reply_markup=kb, parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return tracked_msg
        except Exception as e:
            print(f"[send_payment_to_admins] FAILED target={target_id} payment={payment_id} error={e}")
            return None

    for admin_id in ADMIN_IDS:
        msg = _send_to_one(admin_id)
        if msg:
            save_payment_admin_message(payment_id, admin_id, msg.message_id)

    # Also notify sub-admins with approve_payments permission
    for row in get_all_admin_users():
        sub_id = row["user_id"]
        if sub_id in ADMIN_IDS:
            continue
        perms = json.loads(row["permissions"] or "{}")
        if not (perms.get("full") or perms.get("approve_payments")):
            continue
        msg = _send_to_one(sub_id)
        if msg:
            save_payment_admin_message(payment_id, sub_id, msg.message_id)

    # Group topic: send text first, then photo separately if present
    grp_msg = send_to_topic("payment_approval", text, reply_markup=kb)
    if file_id and grp_msg:
        send_photo_to_topic("payment_approval", file_id, caption="🖼 رسید کاربر")
    elif file_id:
        send_photo_to_topic("payment_approval", file_id, caption=text[:1024])


# ── Card payment approval / rejection ─────────────────────────────────────────
def _clear_payment_admin_buttons(payment_id, status_text):
    """Remove approve/reject buttons from all admin notification messages."""
    msgs = get_payment_admin_messages(payment_id)
    for row in msgs:
        try:
            bot.edit_message_reply_markup(row["admin_id"], row["message_id"], reply_markup=None)
        except Exception:
            pass
        try:
            bot.send_message(row["admin_id"], status_text, parse_mode="HTML")
        except Exception:
            pass
    delete_payment_admin_messages(payment_id)


def finish_card_payment_approval(payment_id, admin_note, approved):
    result = _finish_card_payment_approval_inner(payment_id, admin_note, approved)
    if result:
        status_text = "✅ <b>تراکنش تأیید شد.</b>" if approved else "❌ <b>تراکنش رد شد.</b>"
        try:
            _clear_payment_admin_buttons(payment_id, status_text)
        except Exception:
            pass
    return result


def _finish_card_payment_approval_inner(payment_id, admin_note, approved):
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
            if not complete_payment(payment_id):
                return False  # already processed
            update_balance(user_id, payment["amount"])
            bot.send_message(user_id, f"✅ واریزی شما تأیید شد.\n\n{esc(admin_note)}")
            user_row = get_user(user_id)
            send_to_topic("wallet_log",
                f"💰 <b>شارژ کیف‌پول تأیید شد</b>\n\n"
                f"👤 {esc(user_row['full_name'] if user_row else str(user_id))}\n"
                f"🆔 <code>{user_id}</code>\n"
                f"💵 مبلغ: {fmt_price(payment['amount'])} تومان"
            )

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
            try:
                purchase_id = assign_config_to_user(
                    config_id, user_id, package_id, payment["amount"],
                    payment["payment_method"], is_test=0
                )
            except RuntimeError as e:
                # Concurrent approval: this config was just sold to someone else.
                # Re-reserve and try next available config.
                from .config import ADMIN_IDS as _AIDS
                for _aid in _AIDS:
                    try:
                        bot.send_message(
                            _aid,
                            f"⚠️ <b>تداخل تأیید همزمان</b>\n\n"
                            f"کانفیگ #{config_id} قبلاً به کاربر دیگری اختصاص داده شده بود.\n"
                            f"پرداخت #{payment_id} (کاربر {user_id}) به pending order تبدیل شد.\n\n"
                            f"<code>{e}</code>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                pending_id = create_pending_order(
                    user_id, package_id, payment_id, payment["amount"], payment["payment_method"]
                )
                complete_payment(payment_id)
                bot.send_message(
                    user_id,
                    "✅ پرداخت شما تأیید شد.\n\n"
                    "⚠️ <b>به دلیل ترافیک بالا، کانفیگ انتخابی موجود نبود.</b>\n"
                    "درخواست شما ثبت شد و به زودی کانفیگ تحویل داده می‌شود.\n"
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
            complete_payment(payment_id)
            bot.send_message(user_id, f"✅ واریزی شما تأیید شد.\n\n{esc(admin_note)}")
            deliver_purchase_message(user_id, purchase_id)
            admin_purchase_notify(payment["payment_method"], get_user(user_id), package_row, purchase_id=purchase_id)

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
