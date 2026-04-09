# -*- coding: utf-8 -*-
import json
import time
import threading
import traceback
import urllib.parse
from datetime import datetime
from telebot import types
from ..config import ADMIN_IDS, ADMIN_PERMS, PERM_FULL_SET, PERM_USER_FULL, CRYPTO_COINS, CRYPTO_API_SYMBOLS, CONFIGS_PER_PAGE
from ..bot_instance import bot
from ..helpers import (
    esc, fmt_price, fmt_vol, fmt_dur, now_str, display_name, display_username, safe_support_url,
    is_admin, admin_has_perm, back_button,
    state_set, state_clear, state_name, state_data, parse_int, normalize_text_number,
)
from ..db import (
    setting_get, setting_set,
    ensure_user, get_user, get_users, count_all_users, set_user_status,
    set_user_agent, update_balance, get_user_detail, get_user_purchases,
    get_purchase, get_available_configs_for_package,
    get_all_types, get_active_types, get_type, add_type, update_type, update_type_description, update_type_active, delete_type,
    get_packages, get_package, add_package, update_package_field, toggle_package_active, delete_package,
    get_registered_packages_stock, get_configs_paginated, count_configs,
    expire_config, add_config,
    assign_config_to_user, reserve_first_config, release_reserved_config,
    update_config_field,
    get_payment, create_payment, approve_payment, reject_payment, complete_payment,
    get_agency_price, set_agency_price,
    get_agency_price_config, set_agency_price_config,
    get_agency_type_discount, set_agency_type_discount,
    get_agencies,
    get_all_admin_users, get_admin_user, add_admin_user, update_admin_permissions, remove_admin_user,
    get_all_panels, get_panel, add_panel, delete_panel,
    get_panel_packages, add_panel_package, delete_panel_package, update_panel_field,
    get_conn, create_pending_order, get_pending_order, add_config, search_users,
    reset_all_free_tests, user_has_any_test,
    get_all_pinned_messages, get_pinned_message, add_pinned_message,
    update_pinned_message, delete_pinned_message,
    save_pinned_send, get_pinned_sends, delete_pinned_sends,
    save_payment_admin_message, get_payment_admin_messages, delete_payment_admin_messages,
    save_agency_request_message, get_agency_request_messages, delete_agency_request_messages,
)
from ..gateways.base import is_gateway_available, is_card_info_complete, get_gateway_range_text, is_gateway_in_range, build_gateway_range_guide
from ..gateways.crypto import fetch_crypto_prices
from ..gateways.tetrapay import create_tetrapay_order, verify_tetrapay_order
from ..gateways.swapwallet_crypto import (
    create_swapwallet_crypto_invoice, check_swapwallet_crypto_invoice,
    show_swapwallet_crypto_page,
)
from ..gateways.tronpays_rial import (
    create_tronpays_rial_invoice, check_tronpays_rial_invoice, is_tronpays_paid,
)
from ..ui.helpers import send_or_edit, check_channel_membership, channel_lock_message
from ..ui.keyboards import kb_main, kb_admin_panel
from ..ui.menus import show_main_menu, show_profile, show_support, show_my_configs, show_referral_menu
from ..ui.notifications import (
    deliver_purchase_message, admin_purchase_notify, admin_renewal_notify,
    notify_pending_order_to_admins, _complete_pending_order, auto_fulfill_pending_orders,
)
from ..group_manager import (
    ensure_group_topics, reset_and_recreate_topics, get_group_id,
    _count_active_topics, TOPICS, send_to_topic, log_admin_action,
)
from ..payments import (
    get_effective_price, show_payment_method_selection,
    show_crypto_selection, show_crypto_payment_info,
    send_payment_to_admins, finish_card_payment_approval,
)
from ..admin.renderers import (
    _show_admin_types, _show_admin_stock, _show_admin_admins_panel,
    _show_perm_selection, _show_admin_users_list, _show_admin_user_detail,
    _show_admin_user_detail_msg, _show_admin_assign_config_type, _fake_call,
    _show_admin_panels, _show_panel_packages, _show_panel_edit,
)
from ..admin.backup import _send_backup


def _get_bulk_page_ids(sd):
    """Return config IDs for the current page of a bulk selection state."""
    kind   = sd.get("kind", "av")
    scope  = sd.get("scope", "pk")
    pkg_id = int(sd.get("pkg_id", 0))
    page   = int(sd.get("page", 0))
    offset = page * CONFIGS_PER_PAGE
    with get_conn() as conn:
        if scope == "pk":
            if kind == "sl":
                rows = conn.execute(
                    "SELECT id FROM configs WHERE package_id=? AND sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pkg_id, CONFIGS_PER_PAGE, offset)).fetchall()
            elif kind == "ex":
                rows = conn.execute(
                    "SELECT id FROM configs WHERE package_id=? AND is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pkg_id, CONFIGS_PER_PAGE, offset)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pkg_id, CONFIGS_PER_PAGE, offset)).fetchall()
        else:
            if kind == "sl":
                rows = conn.execute(
                    "SELECT id FROM configs WHERE sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)).fetchall()
            elif kind == "ex":
                rows = conn.execute(
                    "SELECT id FROM configs WHERE is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)).fetchall()
    return [r["id"] for r in rows]


def _render_bulk_page(call, uid):
    """Render the bulk selection page for stock/config management."""
    sd       = state_data(uid)
    kind     = sd.get("kind", "av")   # av / sl / ex
    scope    = sd.get("scope", "pk")  # pk / all
    pkg_id   = int(sd.get("pkg_id", 0))
    page     = int(sd.get("page", 0))
    sel_raw  = sd.get("selected", "")
    selected = set(int(x) for x in sel_raw.split(",") if x.strip().lstrip("-").isdigit())
    offset   = page * CONFIGS_PER_PAGE

    with get_conn() as conn:
        if scope == "pk":
            if kind == "sl":
                cfgs  = conn.execute(
                    "SELECT id, service_name, sold_to, is_expired FROM configs WHERE package_id=? AND sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pkg_id, CONFIGS_PER_PAGE, offset)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL", (pkg_id,)).fetchone()["n"]
            elif kind == "ex":
                cfgs  = conn.execute(
                    "SELECT id, service_name, sold_to, is_expired FROM configs WHERE package_id=? AND is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pkg_id, CONFIGS_PER_PAGE, offset)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND is_expired=1", (pkg_id,)).fetchone()["n"]
            else:
                cfgs  = conn.execute(
                    "SELECT id, service_name, sold_to, is_expired FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pkg_id, CONFIGS_PER_PAGE, offset)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0", (pkg_id,)).fetchone()["n"]
        else:
            if kind == "sl":
                cfgs  = conn.execute(
                    "SELECT id, service_name, sold_to, is_expired FROM configs WHERE sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE sold_to IS NOT NULL").fetchone()["n"]
            elif kind == "ex":
                cfgs  = conn.execute(
                    "SELECT id, service_name, sold_to, is_expired FROM configs WHERE is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE is_expired=1").fetchone()["n"]
            else:
                cfgs  = conn.execute(
                    "SELECT id, service_name, sold_to, is_expired FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0").fetchone()["n"]

    total_pages = max(1, (total + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE)
    page_ids    = [c["id"] for c in cfgs]
    all_sel     = bool(page_ids) and all(cid in selected for cid in page_ids)

    kb = types.InlineKeyboardMarkup()
    for c in cfgs:
        mark     = "✅" if c["id"] in selected else "⬜️"
        svc_name = urllib.parse.unquote(c["service_name"] or "")
        kb.add(types.InlineKeyboardButton(f"{mark} {svc_name}", callback_data=f"adm:stk:btog:{c['id']}"))

    if not all_sel:
        kb.add(types.InlineKeyboardButton("☑️ انتخاب همه این صفحه", callback_data="adm:stk:bsall"))
    else:
        kb.add(types.InlineKeyboardButton("🔲 لغو انتخاب این صفحه", callback_data="adm:stk:bclr"))
    if selected:
        kb.add(types.InlineKeyboardButton("🚫 لغو همه انتخاب‌ها", callback_data="adm:stk:bclrall"))

    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton("⬅️ قبل", callback_data=f"adm:stk:bnav:{page-1}"))
    nav_row.append(types.InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton("بعد ➡️", callback_data=f"adm:stk:bnav:{page+1}"))
    if len(nav_row) > 1:
        kb.row(*nav_row)

    if selected:
        sel_count = len(selected)
        if kind in ("av", "sl"):
            kb.row(
                types.InlineKeyboardButton(f"🗑 حذف ({sel_count})", callback_data="adm:stk:bdel"),
                types.InlineKeyboardButton(f"❌ منقضی ({sel_count})", callback_data="adm:stk:bexp"),
            )
        else:
            kb.add(types.InlineKeyboardButton(f"🗑 حذف ({sel_count})", callback_data="adm:stk:bdel"))

    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:stk:bcanc"))

    kind_labels = {"av": "🟢 موجود", "sl": "🔴 فروخته", "ex": "❌ منقضی"}
    heading = (
        f"☑️ <b>انتخاب گروهی — {kind_labels.get(kind, '')}</b>\n\n"
        f"✅ {len(selected)} مورد انتخاب شده | صفحه {page+1}/{total_pages} از {total} کانفیگ"
    )
    send_or_edit(call, heading, kb)


# ── Per-user callback serialisation ──────────────────────────────────────────
# Prevents a user from triggering the same handler multiple times concurrently
# by rapid-clicking.  Only one callback per user is processed at a time;
# additional clicks while the lock is held are silently answered and dropped.
_USER_CB_LOCKS: dict = {}
_USER_CB_LOCKS_MUTEX = threading.Lock()

def _get_user_cb_lock(uid: int) -> threading.Lock:
    with _USER_CB_LOCKS_MUTEX:
        if uid not in _USER_CB_LOCKS:
            _USER_CB_LOCKS[uid] = threading.Lock()
        return _USER_CB_LOCKS[uid]

# Callbacks that are purely visual / informational and need no deduplication.
_PASSTHROUGH_CALLBACKS = frozenset({"noop", "check_channel"})


@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid  = call.from_user.id
    data = call.data or ""

    # Fast-path: purely informational callbacks bypass the lock entirely.
    if data in _PASSTHROUGH_CALLBACKS:
        if data == "check_channel":
            ensure_user(call.from_user)
            if check_channel_membership(uid):
                bot.answer_callback_query(call.id, "✅ عضویت تأیید شد!")
                show_main_menu(call)
            else:
                bot.answer_callback_query(call.id, "❌ هنوز عضو کانال نشده‌اید.", show_alert=True)
        else:
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
        return

    # Acquire per-user lock (non-blocking).
    # If another callback for this user is already being processed, drop this one.
    lock = _get_user_cb_lock(uid)
    if not lock.acquire(blocking=False):
        try:
            bot.answer_callback_query(call.id, "⏳ لطفاً صبر کنید...", show_alert=False)
        except Exception:
            pass
        return

    try:
        ensure_user(call.from_user)

        if not check_channel_membership(uid):
            bot.answer_callback_query(call.id)
            channel_lock_message(call)
            return

        # Restricted user check (admins bypass)
        if not is_admin(uid):
            _u = get_user(uid)
            if _u and _u["status"] == "restricted":
                bot.answer_callback_query(
                    call.id,
                    "🚫 شما از ربات محدود شده‌اید و نمی‌توانید از آن استفاده کنید.",
                    show_alert=True
                )
                return

        try:
            _dispatch_callback(call, uid, data)
        except Exception as e:
            import traceback as _tb
            err_detail = _tb.format_exc()
            print("CALLBACK_ERROR:", e)
            print(err_detail)
            try:
                short = str(e)[:120]
                bot.answer_callback_query(call.id, f"⚠️ خطا: {short}", show_alert=True)
            except Exception:
                try:
                    bot.answer_callback_query(call.id, "خطایی رخ داد.", show_alert=True)
                except Exception:
                    pass
    finally:
        lock.release()


def _swapwallet_error_inline(call, err_msg):
    """نمایش خطای SwapWallet به صورت inline با راهنمای تنظیمات."""
    if "APPLICATION_NOT_FOUND" in err_msg or "Application not found" in err_msg or "کسب\u200cوکار" in err_msg:
        msg = (
            "❌ <b>خطا: کسب\u200cوکار یافت نشد</b>\n\n"
            "درگاه SwapWallet نیاز به یک <b>Application (کسب\u200cوکار)</b> جداگانه دارد.\n"
            "اکانت شخصی برای دریافت پرداخت کار نمی\u200cکند.\n\n"
            "<b>مراحل رفع:</b>\n"
            "1\ufe0f\u20e3 ربات @SwapWalletBot را باز کنید\n"
            "2\ufe0f\u20e3 به بخش <b>کسب\u200cوکار</b> بروید\n"
            "3\ufe0f\u20e3 یک کسب\u200cوکار جدید بسازید\n"
            "4\ufe0f\u20e3 <b>نام کاربری آن کسب\u200cوکار</b> را در پنل ادمین ← درگاه\u200cها وارد کنید"
        )
    else:
        msg = f"❌ <b>خطا در اتصال به SwapWallet</b>\n\n<code>{err_msg[:300]}</code>"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        bot.edit_message_text(
            msg,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except Exception:
        try:
            bot.send_message(call.message.chat.id, msg, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


# ── TetraPay auto-verify thread ───────────────────────────────────────────────
def _tetrapay_auto_verify(payment_id, authority, uid, chat_id, message_id, kind,
                          package_id=None):
    """Background thread: polls TetraPay every 5s for up to 60 minutes."""
    max_tries = 720  # 720 × 5s = 60 minutes
    for _ in range(max_tries):
        time.sleep(5)
        payment = get_payment(payment_id)
        if not payment or payment["status"] != "pending":
            return  # Already processed by another path
        success, _ = verify_tetrapay_order(authority)
        if not success:
            continue
        # Payment confirmed — process it
        try:
            if kind == "wallet_charge":
                if not complete_payment(payment_id):  # atomic: only one thread wins
                    return
                update_balance(uid, payment["amount"])
                state_clear(uid)
                try:
                    bot.edit_message_text(
                        f"✅ پرداخت شما تأیید شد و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان",
                        chat_id, message_id, parse_mode="HTML",
                        reply_markup=back_button("main"))
                except Exception:
                    bot.send_message(uid,
                        f"✅ پرداخت شما تأیید شد و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان",
                        parse_mode="HTML", reply_markup=back_button("main"))

            elif kind == "config_purchase":
                pkg_row = get_package(package_id)
                cfg_id = payment["config_id"]
                if not cfg_id:
                    cfg_id = reserve_first_config(package_id, payment_id)
                if not cfg_id:
                    pending_id = create_pending_order(uid, package_id, payment_id, payment["amount"], "tetrapay")
                    complete_payment(payment_id)
                    state_clear(uid)
                    msg_text = (
                        "✅ پرداخت شما تأیید شد.\n\n"
                        "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                        "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                        "🙏 از صبر شما متشکریم."
                    )
                    try:
                        bot.edit_message_text(msg_text, chat_id, message_id, parse_mode="HTML",
                                              reply_markup=back_button("main"))
                    except Exception:
                        bot.send_message(uid, msg_text, parse_mode="HTML", reply_markup=back_button("main"))
                    notify_pending_order_to_admins(pending_id, uid, pkg_row, payment["amount"], "tetrapay")
                    return
                purchase_id_new = assign_config_to_user(cfg_id, uid, package_id, payment["amount"], "tetrapay", is_test=0)
                complete_payment(payment_id)
                state_clear(uid)
                try:
                    bot.edit_message_text("✅ پرداخت شما تأیید شد و سرویس آماده است.",
                                          chat_id, message_id, parse_mode="HTML",
                                          reply_markup=back_button("main"))
                except Exception:
                    bot.send_message(uid, "✅ پرداخت شما تأیید شد و سرویس آماده است.",
                                     reply_markup=back_button("main"))
                deliver_purchase_message(chat_id, purchase_id_new)
                admin_purchase_notify("TetraPay", get_user(uid), pkg_row)

            elif kind == "renewal":
                pkg_row = get_package(package_id)
                cfg_id = payment["config_id"]
                with get_conn() as conn:
                    row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (cfg_id,)).fetchone()
                pid = row["purchase_id"] if row else 0
                item = get_purchase(pid) if pid else None
                complete_payment(payment_id)
                state_clear(uid)
                msg_text = (
                    "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                    "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                    "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                    "🙏 از صبر و شکیبایی شما متشکریم."
                )
                try:
                    bot.edit_message_text(msg_text, chat_id, message_id, parse_mode="HTML",
                                          reply_markup=back_button("main"))
                except Exception:
                    bot.send_message(uid, msg_text, parse_mode="HTML", reply_markup=back_button("main"))
                if item:
                    admin_renewal_notify(uid, item, pkg_row, payment["amount"], "TetraPay")

        except Exception as e:
            print("TETRAPAY_AUTO_VERIFY_ERROR:", e)
        return  # Processed (success or error)

    # Timeout — not verified after 60 minutes
    payment = get_payment(payment_id)
    if payment and payment["status"] == "pending":
        state_clear(uid)
        verify_cb = f"rpay:tetrapay:verify:{payment_id}" if kind == "renewal" else f"pay:tetrapay:verify:{payment_id}"
        timeout_msg = (
            "⏰ <b>بررسی خودکار پرداخت پایان یافت</b>\n\n"
            "وقتی پرداخت‌تون تو ربات تتراپی تایید شد، دکمه <b>بررسی پرداخت</b> زیر را بزنید "
            "تا پرداخت تأیید شده و ادامه عملیات انجام شود.\n\n"
            "اگر مبلغ از حساب شما کسر شده و پرداخت تأیید نشده، لطفاً با پشتیبانی تماس بگیرید."
        )
        timeout_kb = types.InlineKeyboardMarkup()
        timeout_kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=verify_cb))
        try:
            bot.edit_message_text(timeout_msg, chat_id, message_id, parse_mode="HTML",
                                  reply_markup=timeout_kb)
        except Exception:
            try:
                bot.send_message(uid, timeout_msg, parse_mode="HTML", reply_markup=timeout_kb)
            except Exception:
                pass


def _start_tetrapay_auto_verify(payment_id, authority, uid, chat_id, message_id,
                                kind, package_id=None):
    t = threading.Thread(
        target=_tetrapay_auto_verify,
        args=(payment_id, authority, uid, chat_id, message_id, kind),
        kwargs={"package_id": package_id},
        daemon=True,
    )
    t.start()


# ── TronPays Rial auto-verify thread ──────────────────────────────────────────
def _tronpays_rial_auto_verify(payment_id, invoice_id, uid, chat_id, message_id, kind,
                               package_id=None):
    """Background thread: polls TronPays every 10s for up to 60 minutes."""
    max_tries = 360  # 360 × 10s = 60 minutes
    for _ in range(max_tries):
        time.sleep(10)
        payment = get_payment(payment_id)
        if not payment or payment["status"] != "pending":
            return
        ok, status = check_tronpays_rial_invoice(invoice_id)
        if not ok or not is_tronpays_paid(status):
            continue
        try:
            if kind == "wallet_charge":
                if not complete_payment(payment_id):  # atomic: only one thread wins
                    return
                update_balance(uid, payment["amount"])
                state_clear(uid)
                try:
                    bot.edit_message_text(
                        f"✅ پرداخت شما تأیید شد و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان",
                        chat_id, message_id, parse_mode="HTML",
                        reply_markup=back_button("main"))
                except Exception:
                    bot.send_message(uid,
                        f"✅ پرداخت شما تأیید شد و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان",
                        parse_mode="HTML", reply_markup=back_button("main"))

            elif kind == "config_purchase":
                pkg_row = get_package(package_id)
                cfg_id = payment["config_id"]
                if not cfg_id:
                    cfg_id = reserve_first_config(package_id, payment_id)
                if not cfg_id:
                    pending_id = create_pending_order(uid, package_id, payment_id, payment["amount"], "tronpays_rial")
                    complete_payment(payment_id)
                    state_clear(uid)
                    msg_text = (
                        "✅ پرداخت شما تأیید شد.\n\n"
                        "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                        "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                        "🙏 از صبر شما متشکریم."
                    )
                    try:
                        bot.edit_message_text(msg_text, chat_id, message_id, parse_mode="HTML",
                                              reply_markup=back_button("main"))
                    except Exception:
                        bot.send_message(uid, msg_text, parse_mode="HTML", reply_markup=back_button("main"))
                    notify_pending_order_to_admins(pending_id, uid, pkg_row, payment["amount"], "tronpays_rial")
                    return
                purchase_id_new = assign_config_to_user(cfg_id, uid, package_id, payment["amount"], "tronpays_rial", is_test=0)
                complete_payment(payment_id)
                state_clear(uid)
                try:
                    bot.edit_message_text("✅ پرداخت شما تأیید شد و سرویس آماده است.",
                                          chat_id, message_id, parse_mode="HTML",
                                          reply_markup=back_button("main"))
                except Exception:
                    bot.send_message(uid, "✅ پرداخت شما تأیید شد و سرویس آماده است.",
                                     reply_markup=back_button("main"))
                deliver_purchase_message(chat_id, purchase_id_new)
                admin_purchase_notify("TronPays", get_user(uid), pkg_row)

            elif kind == "renewal":
                pkg_row = get_package(package_id)
                cfg_id = payment["config_id"]
                with get_conn() as conn:
                    row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (cfg_id,)).fetchone()
                pid = row["purchase_id"] if row else 0
                item = get_purchase(pid) if pid else None
                complete_payment(payment_id)
                state_clear(uid)
                msg_text = (
                    "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                    "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                    "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                    "🙏 از صبر و شکیبایی شما متشکریم."
                )
                try:
                    bot.edit_message_text(msg_text, chat_id, message_id, parse_mode="HTML",
                                          reply_markup=back_button("main"))
                except Exception:
                    bot.send_message(uid, msg_text, parse_mode="HTML", reply_markup=back_button("main"))
                if item:
                    admin_renewal_notify(uid, item, pkg_row, payment["amount"], "TronPays")

        except Exception as e:
            print("TRONPAYS_RIAL_AUTO_VERIFY_ERROR:", e)
        return

    # Timeout
    payment = get_payment(payment_id)
    if payment and payment["status"] == "pending":
        state_clear(uid)
        verify_cb = f"rpay:tronpays_rial:verify:{payment_id}" if kind == "renewal" else f"pay:tronpays_rial:verify:{payment_id}"
        timeout_msg = (
            "⏰ <b>بررسی خودکار پرداخت پایان یافت</b>\n\n"
            "وقتی پرداخت‌تون تو TronPays تایید شد، دکمه <b>بررسی پرداخت</b> زیر را بزنید "
            "تا پرداخت تأیید شده و ادامه عملیات انجام شود.\n\n"
            "اگر مبلغ از حساب شما کسر شده و پرداخت تأیید نشده، لطفاً با پشتیبانی تماس بگیرید."
        )
        timeout_kb = types.InlineKeyboardMarkup()
        timeout_kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=verify_cb))
        try:
            bot.edit_message_text(timeout_msg, chat_id, message_id, parse_mode="HTML",
                                  reply_markup=timeout_kb)
        except Exception:
            try:
                bot.send_message(uid, timeout_msg, parse_mode="HTML", reply_markup=timeout_kb)
            except Exception:
                pass


def _start_tronpays_rial_auto_verify(payment_id, invoice_id, uid, chat_id, message_id,
                                     kind, package_id=None):
    t = threading.Thread(
        target=_tronpays_rial_auto_verify,
        args=(payment_id, invoice_id, uid, chat_id, message_id, kind),
        kwargs={"package_id": package_id},
        daemon=True,
    )
    t.start()


def _dispatch_callback(call, uid, data):
    # Navigation
    if data.startswith("nav:"):
        target = data[4:]
        state_clear(uid)
        bot.answer_callback_query(call.id)
        if target == "main":
            show_main_menu(call)
        else:
            _fake_call(call, target)
        return

    if data == "profile":
        bot.answer_callback_query(call.id)
        show_profile(call, uid)
        return

    if data == "support":
        bot.answer_callback_query(call.id)
        show_support(call)
        return

    if data == "referral:menu":
        bot.answer_callback_query(call.id)
        show_referral_menu(call, uid)
        return

    # ── Agency request ────────────────────────────────────────────────────────
    if data == "agency:request":
        user = get_user(uid)
        if user and user["is_agent"]:
            bot.answer_callback_query(call.id, "شما در حال حاضر نماینده هستید.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📤 ارسال درخواست (بدون متن)", callback_data="agency:send_empty"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        state_set(uid, "agency_request_text")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🤝 <b>درخواست نمایندگی</b>\n\n"
            "لطفاً متن درخواست خود را ارسال کنید. موارد زیر را در متن ذکر کنید:\n\n"
            "📊 میزان فروش شما در روز یا هفته\n"
            "📢 کانال یا فروشگاهی که دارید (آدرس کانال تلگرام)\n"
            "🎧 آیدی پشتیبانی مجموعه شما\n"
            "📝 هر توضیح دیگری که لازم می‌دانید\n\n"
            "اگر نمی‌خواهید متنی بنویسید، دکمه زیر را بزنید:", kb)
        return

    if data == "agency:send_empty":
        state_clear(uid)
        user = get_user(uid)
        if user and user["is_agent"]:
            bot.answer_callback_query(call.id, "شما در حال حاضر نماینده هستید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        send_or_edit(call, "✅ درخواست نمایندگی شما ارسال شد.\n⏳ لطفاً منتظر بررسی ادمین باشید.", back_button("main"))
        # Notify admins
        text = (
            f"🤝 <b>درخواست نمایندگی جدید</b>\n\n"
            f"👤 نام: {esc(user['full_name'])}\n"
            f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
            f"🔢 آیدی: <code>{user['user_id']}</code>\n\n"
            f"📝 متن درخواست: <i>بدون متن</i>"
        )
        admin_kb = types.InlineKeyboardMarkup()
        admin_kb.row(
            types.InlineKeyboardButton("✅ تأیید", callback_data=f"agency:approve_now:{uid}"),
            types.InlineKeyboardButton("❌ رد", callback_data=f"agency:reject_now:{uid}"),
        )
        for admin_id in ADMIN_IDS:
            try:
                msg = bot.send_message(admin_id, text, reply_markup=admin_kb)
                save_agency_request_message(uid, admin_id, msg.message_id)
            except Exception:
                pass
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            import json as _json
            perms = _json.loads(row["permissions"] or "{}")
            if not (perms.get("full") or perms.get("agency")):
                continue
            try:
                msg = bot.send_message(sub_id, text, reply_markup=admin_kb)
                save_agency_request_message(uid, sub_id, msg.message_id)
            except Exception:
                pass
        if setting_get("notif_own_agency_request", "1") == "1" or True:
            grp_msg = send_to_topic("agency_request", text, reply_markup=admin_kb)
            if grp_msg:
                save_agency_request_message(uid, grp_msg.chat.id, grp_msg.message_id)
        return

    if data.startswith("agency:approve:"):
        if not is_admin(uid) or not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        state_set(uid, "agency_approve_note", target_user_id=target_uid)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏭ بدون پیام", callback_data=f"agency:approve_now:{target_uid}"))
        bot.send_message(call.message.chat.id,
            f"✅ در حال تأیید نمایندگی کاربر <code>{target_uid}</code>\n\n"
            "اگر می‌خواهید پیامی برای کاربر ارسال کنید، متن را بنویسید.\n"
            "در غیر این صورت دکمه زیر را بزنید:", reply_markup=kb)
        return

    if data.startswith("agency:approve_now:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        state_clear(uid)
        with get_conn() as conn:
            conn.execute("UPDATE users SET is_agent=1 WHERE user_id=?", (target_uid,))
        default_pct = int(setting_get("agency_default_discount_pct", "20") or "20")
        if default_pct > 0:
            set_agency_price_config(target_uid, "global", "pct", default_pct)
        bot.answer_callback_query(call.id, "✅ نمایندگی تأیید شد.")
        # Remove buttons from all tracked messages
        for row in get_agency_request_messages(target_uid):
            try:
                bot.edit_message_reply_markup(row["chat_id"], row["message_id"], reply_markup=None)
            except Exception:
                pass
        delete_agency_request_messages(target_uid)
        # Notify user
        try:
            bot.send_message(target_uid,
                "🎉 <b>درخواست نمایندگی شما تأیید شد!</b>\n\nاکنون شما نماینده هستید.",
                parse_mode="HTML")
        except Exception:
            pass
        # Log to agency_log topic
        user_row = get_user(target_uid)
        send_to_topic("agency_log",
            f"✅ <b>نمایندگی تأیید شد</b>\n\n"
            f"👤 نام: {esc(user_row['full_name'] if user_row else str(target_uid))}\n"
            f"🆔 نام کاربری: {esc(user_row['username'] or 'ندارد' if user_row else '-')}\n"
            f"🆔 آیدی: <code>{target_uid}</code>\n"
            f"📊 تخفیف پیش‌فرض: <b>{default_pct}%</b>\n"
            f"تأییدکننده: <code>{uid}</code>"
        )
        # If called from admin DM, show user detail panel
        if call.message.chat.type == "private":
            _show_admin_user_detail(call, target_uid)
        else:
            try:
                bot.send_message(call.message.chat.id,
                    f"✅ نمایندگی کاربر <code>{target_uid}</code> تأیید شد.",
                    message_thread_id=call.message.message_thread_id,
                    parse_mode="HTML")
            except Exception:
                pass
        return

    if data.startswith("agency:reject_now:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        bot.answer_callback_query(call.id, "❌ رد شد.")
        # Remove buttons from all tracked messages
        for row in get_agency_request_messages(target_uid):
            try:
                bot.edit_message_reply_markup(row["chat_id"], row["message_id"], reply_markup=None)
            except Exception:
                pass
        delete_agency_request_messages(target_uid)
        # Notify user
        try:
            bot.send_message(target_uid,
                "❌ <b>درخواست نمایندگی شما رد شد.</b>",
                parse_mode="HTML")
        except Exception:
            pass
        # Log to agency_log topic
        user_row = get_user(target_uid)
        send_to_topic("agency_log",
            f"❌ <b>نمایندگی رد شد</b>\n\n"
            f"👤 نام: {esc(user_row['full_name'] if user_row else str(target_uid))}\n"
            f"🆔 آیدی: <code>{target_uid}</code>\n"
            f"ردکننده: <code>{uid}</code>"
        )
        return

    if data.startswith("agency:reject:"):
        if not is_admin(uid) or not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        state_set(uid, "agency_reject_reason", target_user_id=target_uid)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.message.chat.id,
            f"❌ در حال رد درخواست نمایندگی کاربر <code>{target_uid}</code>\n\n"
            "لطفاً دلیل رد را بنویسید:")
        return

    if data == "my_configs":
        bot.answer_callback_query(call.id)
        show_my_configs(call, uid)
        return

    if data.startswith("mycfg:"):
        purchase_id = int(data.split(":")[1])
        item = get_purchase(purchase_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        deliver_purchase_message(call.message.chat.id, purchase_id)
        return

    # ── Renewal flow ──────────────────────────────────────────────────────────
    if data.startswith("renew:") and not data.startswith("renew:p:") and not data.startswith("renew:confirm:"):
        if setting_get("manual_renewal_enabled", "1") != "1" and not is_admin(uid):
            bot.answer_callback_query(call.id, "⛔ تمدید در حال حاضر غیرفعال است.", show_alert=True)
            return
        purchase_id = int(data.split(":")[1])
        item = get_purchase(purchase_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        # Show packages of same type for renewal
        with get_conn() as conn:
            type_id = conn.execute("SELECT type_id FROM packages WHERE id=?", (item["package_id"],)).fetchone()["type_id"]
        packages = [p for p in get_packages(type_id=type_id) if p["price"] > 0]
        kb = types.InlineKeyboardMarkup()
        user = get_user(uid)
        for p in packages:
            price = get_effective_price(uid, p)
            title = f"{p['name']} | {fmt_vol(p['volume_gb'])} | {fmt_dur(p['duration_days'])} | {fmt_price(price)} ت"
            kb.add(types.InlineKeyboardButton(title, callback_data=f"renew:p:{purchase_id}:{p['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"mycfg:{purchase_id}"))
        bot.answer_callback_query(call.id)
        agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""
        if not packages:
            send_or_edit(call, "📭 در حال حاضر پکیجی برای تمدید موجود نیست.", kb)
        else:
            send_or_edit(call, f"♻️ <b>تمدید سرویس</b>\n\nپکیج مورد نظر برای تمدید را انتخاب کنید:{agent_note}", kb)
        return

    if data.startswith("renew:p:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        package_row = get_package(package_id)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        state_set(uid, "renew_select_method",
                  package_id=package_id, amount=price,
                  kind="renewal", purchase_id=purchase_id)
        _gw_labels = []
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💰 پرداخت از موجودی", callback_data=f"rpay:wallet:{purchase_id}:{package_id}"))
        if is_gateway_available("card", uid) and is_card_info_complete():
            _lbl = setting_get("gw_card_display_name", "").strip() or "💳 کارت به کارت"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"rpay:card:{purchase_id}:{package_id}"))
            _gw_labels.append(("card", _lbl))
        if is_gateway_available("crypto", uid):
            _lbl = setting_get("gw_crypto_display_name", "").strip() or "💎 ارز دیجیتال"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"rpay:crypto:{purchase_id}:{package_id}"))
            _gw_labels.append(("crypto", _lbl))
        if is_gateway_available("tetrapay", uid):
            _lbl = setting_get("gw_tetrapay_display_name", "").strip() or "💳 درگاه کارت به کارت (TetraPay)"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"rpay:tetrapay:{purchase_id}:{package_id}"))
            _gw_labels.append(("tetrapay", _lbl))
        if is_gateway_available("swapwallet_crypto", uid):
            _lbl = setting_get("gw_swapwallet_crypto_display_name", "").strip() or "💳 درگاه کارت به کارت و ارز دیجیتال (SwapWallet)"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"rpay:swapwallet_crypto:{purchase_id}:{package_id}"))
            _gw_labels.append(("swapwallet_crypto", _lbl))
        if is_gateway_available("tronpays_rial", uid):
            _lbl = setting_get("gw_tronpays_rial_display_name", "").strip() or "💳 درگاه کارت به کارت (TronsPay)"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"rpay:tronpays_rial:{purchase_id}:{package_id}"))
            _gw_labels.append(("tronpays_rial", _lbl))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"renew:{purchase_id}"))
        _range_guide = build_gateway_range_guide(_gw_labels)
        text = (
            "♻️ <b>تمدید سرویس</b>\n\n"
            f"🔮 سرویس فعلی: {esc(urllib.parse.unquote(item['service_name'] or ''))}\n"
            f"📦 پکیج تمدید: {esc(package_row['name'])}\n"
            f"🔋 حجم: {fmt_vol(package_row['volume_gb'])}\n"
            f"⏰ مدت: {fmt_dur(package_row['duration_days'])}\n"
            f"💰 قیمت: {fmt_price(price)} تومان\n\n"
            + (_range_guide + "\n\n" if _range_guide else "")
            + "روش پرداخت را انتخاب کنید:"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    # ── Renewal payment handlers ──────────────────────────────────────────────
    if data.startswith("rpay:wallet:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        user = get_user(uid)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if user["balance"] < price:
            bot.answer_callback_query(call.id, "موجودی کیف پول کافی نیست.", show_alert=True)
            return
        update_balance(uid, -price)
        payment_id = create_payment("renewal", uid, package_id, price, "wallet",
                                     status="completed", config_id=item["config_id"])
        complete_payment(payment_id)
        bot.answer_callback_query(call.id, "پرداخت موفق بود.")
        send_or_edit(call,
            "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
            "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
            "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
            "🙏 از صبر و شکیبایی شما متشکریم.",
            back_button("main"))
        admin_renewal_notify(uid, item, package_row, price, "کیف پول")
        state_clear(uid)
        return

    if data.startswith("rpay:card:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        card  = setting_get("payment_card", "")
        bank  = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        if not card:
            bot.answer_callback_query(call.id, "اطلاعات پرداخت هنوز ثبت نشده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("card", price):
            _rng = get_gateway_range_text("card")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای این درگاه مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        payment_id = create_payment("renewal", uid, package_id, price, "card", status="pending",
                                     config_id=item["config_id"])
        state_set(uid, "await_renewal_receipt", payment_id=payment_id, purchase_id=purchase_id)
        text = (
            "💳 <b>کارت به کارت (تمدید)</b>\n\n"
            f"لطفاً مبلغ <b>{fmt_price(price)}</b> تومان را به کارت زیر واریز کنید:\n\n"
            f"🏦 {esc(bank or 'ثبت نشده')}\n"
            f"👤 {esc(owner or 'ثبت نشده')}\n"
            f"💳 <code>{esc(card)}</code>\n\n"
            "📸 پس از واریز، تصویر رسید یا شماره پیگیری را ارسال کنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("rpay:crypto:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("crypto", price):
            _rng = get_gateway_range_text("crypto")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای این درگاه مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        state_set(uid, "renew_crypto_select_coin", package_id=package_id, amount=price,
                  purchase_id=purchase_id, config_id=item["config_id"])
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=price)
        return

    if data.startswith("rpay:tetrapay:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        authority = payment["receipt_text"]
        success, result = verify_tetrapay_order(authority)
        if success:
            complete_payment(payment_id)
            package_row = get_package(payment["package_id"])
            config_id = payment["config_id"]
            with get_conn() as conn:
                row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (config_id,)).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item = get_purchase(purchase_id) if purchase_id else None
            bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
            send_or_edit(call,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم.",
                back_button("main"))
            if item:
                admin_renewal_notify(uid, item, package_row, payment["amount"], "TetraPay")
            state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("rpay:tetrapay:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("tetrapay", price):
            _rng = get_gateway_range_text("tetrapay")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای درگاه TetraPay مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        hash_id = f"rnw-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_tetrapay_order(price, hash_id, f"تمدید {package_row['name']}")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد درخواست پرداخت آنلاین.", show_alert=True)
            return
        authority = result.get("Authority", "")
        pay_url_bot = result.get("payment_url_bot", "")
        pay_url_web = result.get("payment_url_web", "")
        payment_id = create_payment("renewal", uid, package_id, price, "tetrapay", status="pending",
                                     config_id=item["config_id"])
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (authority, payment_id))
        state_set(uid, "await_renewal_tetrapay_verify", payment_id=payment_id, authority=authority,
                  purchase_id=purchase_id)
        text = (
            "🏦 <b>پرداخت آنلاین (تمدید)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n\n"
            "لطفاً از یکی از لینک‌های زیر پرداخت را انجام دهید.\n\n"
            "⏳ <b>تا یک ساعت</b> اگر پرداخت‌تون تایید بشه به صورت خودکار عملیات انجام می‌شود.\n"
            "در غیر این صورت دکمه <b>بررسی پرداخت</b> را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        if pay_url_bot and setting_get("tetrapay_mode_bot", "1") == "1":
            kb.add(types.InlineKeyboardButton("💳 پرداخت در تلگرام", url=pay_url_bot))
        if pay_url_web and setting_get("tetrapay_mode_web", "1") == "1":
            kb.add(types.InlineKeyboardButton("🌐 پرداخت در مرورگر", url=pay_url_web))
        kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=f"rpay:tetrapay:verify:{payment_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        _start_tetrapay_auto_verify(
            payment_id, authority, uid,
            call.message.chat.id, call.message.message_id,
            "renewal", package_id=package_id)
        return

    if data.startswith("rpay:tetrapay:verify:"):
        # NOTE: this block is now unreachable (handled above) — kept as safety guard
        bot.answer_callback_query(call.id)
        return

    # ── TronPays Rial: renewal ────────────────────────────────────────────────
    if data.startswith("rpay:tronpays_rial:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        ok, status = check_tronpays_rial_invoice(invoice_id)
        if not ok:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        if is_tronpays_paid(status):
            complete_payment(payment_id)
            package_row = get_package(payment["package_id"])
            config_id   = payment["config_id"]
            with get_conn() as conn:
                row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (config_id,)).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item = get_purchase(purchase_id) if purchase_id else None
            bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
            send_or_edit(call,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم.",
                back_button("main"))
            if item:
                admin_renewal_notify(uid, item, package_row, payment["amount"], "TronPays")
            state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("rpay:tronpays_rial:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("tronpays_rial", price):
            _rng = get_gateway_range_text("tronpays_rial")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای درگاه TronsPay مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        hash_id = f"rnw-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_tronpays_rial_invoice(price, hash_id, f"تمدید {package_row['name']}")
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"⚠️ <b>خطا در ایجاد درگاه TronPays</b>\n\n"
                f"<code>{esc(err_msg[:400])}</code>\n\n"
                "💡 مطمئن شوید کلید API صحیح وارد شده باشد.",
                back_button(f"renew:{purchase_id}"))
            return
        invoice_id = result["invoice_id"]
        invoice_url = result["invoice_url"]
        payment_id = create_payment("renewal", uid, package_id, price, "tronpays_rial", status="pending",
                                    config_id=item["config_id"])
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_renewal_tronpays_rial_verify", payment_id=payment_id,
                  invoice_id=invoice_id, purchase_id=purchase_id)
        text = (
            "💳 <b>پرداخت ریالی (TronPays) — تمدید</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n\n"
            "از لینک زیر پرداخت را انجام دهید.\n\n"
            "⏳ <b>تا یک ساعت</b> پرداخت به صورت خودکار بررسی می‌شود.\n"
            "در غیر این صورت دکمه «بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💳 پرداخت از درگاه TronPays", url=invoice_url))
        kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=f"rpay:tronpays_rial:verify:{payment_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        _start_tronpays_rial_auto_verify(
            payment_id, invoice_id, uid,
            call.message.chat.id, call.message.message_id,
            "renewal", package_id=package_id)
        return

    # ── Admin: Confirm renewal ────────────────────────────────────────────────
    if data.startswith("renew:confirm:"):
        if not admin_has_perm(uid, "approve_renewal"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        parts = data.split(":")
        config_id  = int(parts[2])
        target_uid = int(parts[3])
        # Un-expire config if it was expired
        with get_conn() as conn:
            conn.execute("UPDATE configs SET is_expired=0 WHERE id=?", (config_id,))
        bot.answer_callback_query(call.id, "✅ تمدید تأیید شد.")
        # Update admin's message
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        try:
            bot.send_message(call.message.chat.id, "✅ تمدید تأیید و به کاربر اطلاع داده شد.")
        except Exception:
            pass
        # Notify user
        svc_name = ""
        try:
            with get_conn() as conn:
                cfg_row = conn.execute(
                    "SELECT c.service_name, c.package_id, p.name AS package_name, "
                    "p.volume_gb, p.duration_days, p.price, t.name AS type_name "
                    "FROM configs c "
                    "JOIN packages p ON p.id = c.package_id "
                    "JOIN config_types t ON t.id = p.type_id "
                    "WHERE c.id=?", (config_id,)
                ).fetchone()
            svc_name = urllib.parse.unquote(cfg_row["service_name"] or "") if cfg_row else ""
            bot.send_message(target_uid,
                f"🎉 <b>تمدید سرویس انجام شد!</b>\n\n"
                f"✅ سرویس <b>{esc(svc_name)}</b> شما با موفقیت تمدید شد.\n"
                "از اعتماد شما سپاسگزاریم. 🙏")
        except Exception:
            pass
        # Renewal log — find the payment method from the original admin message
        renewal_method = ""
        try:
            orig_text = call.message.text or call.message.caption or ""
            if "(" in orig_text and ")" in orig_text:
                renewal_method = orig_text.split("(", 1)[1].split(")", 1)[0]
        except Exception:
            pass
        try:
            user_row = get_user(target_uid)
            log_text = (
                f"🔄 | <b>تمدید تأیید شد</b>"
                f"{(' (' + esc(renewal_method) + ')') if renewal_method else ''}\n\n"
                f"▫️ آیدی کاربر: <code>{target_uid}</code>\n"
                f"👨‍💼 نام: {esc(user_row['full_name'] if user_row else '')}\n"
                f"⚡️ نام کاربری: {esc((user_row['username'] or 'ندارد') if user_row else 'ندارد')}\n"
                f"🔮 نام سرویس: {esc(svc_name or str(config_id))}\n"
            )
            if cfg_row:
                log_text += (
                    f"🚦 سرور: {esc(cfg_row['type_name'])}\n"
                    f"✏️ پکیج: {esc(cfg_row['package_name'])}\n"
                    f"🔋 حجم: {cfg_row['volume_gb']} گیگ\n"
                    f"⏰ مدت: {cfg_row['duration_days']} روز\n"
                    f"💰 قیمت: {fmt_price(cfg_row['price'])} تومان"
                )
            send_to_topic("renewal_log", log_text)
        except Exception:
            pass
        return

    # ── Buy flow ──────────────────────────────────────────────────────────────
    if data == "buy:start":
        # Check purchase rules
        if setting_get("purchase_rules_enabled", "0") == "1":
            accepted = setting_get(f"rules_accepted_{uid}", "0")
            if accepted != "1":
                rules_text = setting_get("purchase_rules_text", "")
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✅ من قوانین را خواندم و پذیرفتم", callback_data="buy:accept_rules"))
                kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
                bot.answer_callback_query(call.id)
                send_or_edit(call, f"📜 <b>قوانین خرید</b>\n\n{esc(rules_text)}", kb)
                return
        # Fall through to actual buy
        data = "buy:start_real"

    if data == "buy:start_real":
        # Check if shop is open
        if setting_get("shop_open", "1") != "1":
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, "🔴 <b>فروشگاه موقتاً تعطیل است.</b>\n\nلطفاً بعداً مراجعه کنید.", kb)
            return
        stock_only = setting_get("preorder_mode", "0") == "1"
        items = get_active_types()
        kb = types.InlineKeyboardMarkup()
        has_any = False
        for item in items:
            if stock_only:
                packs = [p for p in get_packages(type_id=item['id']) if p['price'] > 0 and p['stock'] > 0]
            else:
                packs = [p for p in get_packages(type_id=item['id']) if p['price'] > 0]
            if packs:
                kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data=f"buy:t:{item['id']}"))
                has_any = True
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        if not has_any:
            send_or_edit(call, "📭 در حال حاضر بسته‌ای برای فروش موجود نیست.", kb)
        else:
            send_or_edit(call, "🛒 <b>خرید کانفیگ جدید</b>\n\nنوع مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("buy:t:"):
        type_id   = int(data.split(":")[2])
        stock_only = setting_get("preorder_mode", "0") == "1"
        if stock_only:
            packages = [p for p in get_packages(type_id=type_id) if p["price"] > 0 and p["stock"] > 0]
        else:
            packages = [p for p in get_packages(type_id=type_id) if p["price"] > 0]
        kb   = types.InlineKeyboardMarkup()
        user = get_user(uid)
        for p in packages:
            price = get_effective_price(uid, p)
            stock_tag = "" if p["stock"] > 0 else " ⏳"
            title = f"{p['name']}{stock_tag} | {fmt_vol(p['volume_gb'])} | {fmt_dur(p['duration_days'])} | {fmt_price(price)} ت"
            kb.add(types.InlineKeyboardButton(title, callback_data=f"buy:p:{p['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="buy:start"))
        bot.answer_callback_query(call.id)
        agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""
        if not packages:
            send_or_edit(call, "📭 در حال حاضر بسته‌ای برای فروش در این نوع موجود نیست.", kb)
        else:
            send_or_edit(call, f"📦 یکی از پکیج‌ها را انتخاب کنید:{agent_note}", kb)
        return

    if data.startswith("buy:p:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        state_set(uid, "buy_select_method",
                  package_id=package_id, amount=price,
                  kind="config_purchase")
        _gw_labels = []
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💰 پرداخت از موجودی", callback_data=f"pay:wallet:{package_id}"))
        if is_gateway_available("card", uid) and is_card_info_complete():
            _lbl = setting_get("gw_card_display_name", "").strip() or "💳 کارت به کارت"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"pay:card:{package_id}"))
            _gw_labels.append(("card", _lbl))
        if is_gateway_available("crypto", uid):
            _lbl = setting_get("gw_crypto_display_name", "").strip() or "💎 ارز دیجیتال"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"pay:crypto:{package_id}"))
            _gw_labels.append(("crypto", _lbl))
        if is_gateway_available("tetrapay", uid):
            _lbl = setting_get("gw_tetrapay_display_name", "").strip() or "💳 درگاه کارت به کارت (TetraPay)"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"pay:tetrapay:{package_id}"))
            _gw_labels.append(("tetrapay", _lbl))
        if is_gateway_available("swapwallet_crypto", uid):
            _lbl = setting_get("gw_swapwallet_crypto_display_name", "").strip() or "💳 درگاه کارت به کارت و ارز دیجیتال (SwapWallet)"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"pay:swapwallet_crypto:{package_id}"))
            _gw_labels.append(("swapwallet_crypto", _lbl))
        if is_gateway_available("tronpays_rial", uid):
            _lbl = setting_get("gw_tronpays_rial_display_name", "").strip() or "💳 درگاه کارت به کارت (TronsPay)"
            kb.add(types.InlineKeyboardButton(_lbl, callback_data=f"pay:tronpays_rial:{package_id}"))
            _gw_labels.append(("tronpays_rial", _lbl))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"buy:t:{package_row['type_id']}"))
        _range_guide = build_gateway_range_guide(_gw_labels)
        text = (
            "💳 <b>انتخاب روش پرداخت</b>\n\n"
            f"🧩 نوع: {esc(package_row['type_name'])}\n"
            f"📦 پکیج: {esc(package_row['name'])}\n"
            f"🔋 حجم: {fmt_vol(package_row['volume_gb'])}\n"
            f"⏰ مدت: {fmt_dur(package_row['duration_days'])}\n"
            f"💰 قیمت: {fmt_price(price)} تومان\n\n"
            + (_range_guide + "\n\n" if _range_guide else "")
            + "روش پرداخت را انتخاب کنید:"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("pay:wallet:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        user        = get_user(uid)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        preorder_on = setting_get("preorder_mode", "0") == "1"
        if preorder_on and package_row["stock"] <= 0:
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if user["balance"] < price:
            bot.answer_callback_query(call.id, "موجودی کیف پول کافی نیست.", show_alert=True)
            return
        config_id = reserve_first_config(package_id)
        if not config_id:
            if preorder_on:
                bot.answer_callback_query(call.id, "فعلاً کانفیگی موجود نیست.", show_alert=True)
                return
            # preorder_mode OFF — deduct balance, create pending order, notify admin
            update_balance(uid, -price)
            payment_id = create_payment("config_purchase", uid, package_id, price, "wallet", status="completed")
            complete_payment(payment_id)
            pending_id = create_pending_order(uid, package_id, payment_id, price, "wallet")
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                "✅ پرداخت شما از کیف پول انجام شد.\n\n"
                "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                "🙏 از صبر شما متشکریم.", back_button("main"))
            notify_pending_order_to_admins(pending_id, uid, package_row, price, "wallet")
            state_clear(uid)
            return
        update_balance(uid, -price)
        try:
            purchase_id = assign_config_to_user(config_id, uid, package_id, price, "wallet", is_test=0)
        except Exception:
            update_balance(uid, price)
            release_reserved_config(config_id)
            bot.answer_callback_query(call.id, "⚠️ خطایی رخ داد، مبلغ به کیف پول بازگردانده شد.", show_alert=True)
            return
        payment_id  = create_payment("config_purchase", uid, package_id, price, "wallet",
                                     status="completed", config_id=config_id)
        complete_payment(payment_id)
        bot.answer_callback_query(call.id, "خرید با موفقیت انجام شد.")
        send_or_edit(call, "✅ خرید شما انجام شد و سرویس در پیام بعدی ارسال می‌شود.", back_button("main"))
        deliver_purchase_message(call.message.chat.id, purchase_id)
        admin_purchase_notify("کیف پول", get_user(uid), package_row)
        state_clear(uid)
        return

    if data.startswith("pay:card:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        card  = setting_get("payment_card", "")
        bank  = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        if not card:
            bot.answer_callback_query(call.id, "اطلاعات پرداخت هنوز ثبت نشده است.", show_alert=True)
            return
        price      = get_effective_price(uid, package_row)
        if not is_gateway_in_range("card", price):
            _rng = get_gateway_range_text("card")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای این درگاه مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        payment_id = create_payment("config_purchase", uid, package_id, price, "card", status="pending")
        state_set(uid, "await_purchase_receipt", payment_id=payment_id)
        text = (
            "💳 <b>کارت به کارت</b>\n\n"
            f"لطفاً مبلغ <b>{fmt_price(price)}</b> تومان را به کارت زیر واریز کنید:\n\n"
            f"🏦 {esc(bank or 'ثبت نشده')}\n"
            f"👤 {esc(owner or 'ثبت نشده')}\n"
            f"💳 <code>{esc(card)}</code>\n\n"
            "📸 پس از واریز، تصویر رسید یا شماره پیگیری را ارسال کنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("pay:crypto:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("crypto", price):
            _rng = get_gateway_range_text("crypto")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای این درگاه مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        state_set(uid, "buy_crypto_select_coin", package_id=package_id, amount=price)
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=price)
        return

    # Crypto coin selection (after buy)
    if data.startswith("pm:crypto:"):
        coin_key = data.split(":")[2]
        sd       = state_data(uid)
        sn       = state_name(uid)
        if sn == "buy_crypto_select_coin":
            package_id  = sd.get("package_id")
            amount      = sd.get("amount")
            package_row = get_package(package_id)
            if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
                bot.answer_callback_query(call.id, "موجودی تمام شده است.", show_alert=True)
                return
            payment_id = create_payment("config_purchase", uid, package_id, amount, "crypto",
                                        status="pending", crypto_coin=coin_key)
            state_set(uid, "await_purchase_receipt", payment_id=payment_id)
            bot.answer_callback_query(call.id)
            show_crypto_payment_info(call, uid, coin_key, amount)
        elif sn == "wallet_crypto_select_coin":
            amount     = sd.get("amount")
            payment_id = sd.get("payment_id") or create_payment("wallet_charge", uid, None, amount, "crypto",
                                                                  status="pending", crypto_coin=coin_key)
            state_set(uid, "await_wallet_receipt", payment_id=payment_id, amount=amount)
            bot.answer_callback_query(call.id)
            show_crypto_payment_info(call, uid, coin_key, amount)
        elif sn == "renew_crypto_select_coin":
            package_id  = sd.get("package_id")
            amount      = sd.get("amount")
            config_id_r = sd.get("config_id")
            purchase_id = sd.get("purchase_id")
            payment_id = create_payment("renewal", uid, package_id, amount, "crypto",
                                        status="pending", crypto_coin=coin_key, config_id=config_id_r)
            state_set(uid, "await_renewal_receipt", payment_id=payment_id, purchase_id=purchase_id)
            bot.answer_callback_query(call.id)
            show_crypto_payment_info(call, uid, coin_key, amount)
        else:
            bot.answer_callback_query(call.id)
        return

    if data == "pm:crypto":
        sd = state_data(uid)
        amount = sd.get("amount")
        if state_name(uid) == "wallet_charge_method":
            payment_id = create_payment("wallet_charge", uid, None, amount, "crypto", status="pending")
            state_set(uid, "wallet_crypto_select_coin", amount=amount, payment_id=payment_id)
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=amount)
        return

    if data == "pm:back":
        bot.answer_callback_query(call.id)
        show_main_menu(call)
        return

    # ── TetraPay ──────────────────────────────────────────────────────────────
    if data.startswith("pay:tetrapay:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        authority = payment["receipt_text"]
        success, result = verify_tetrapay_order(authority)
        if success:
            if payment["kind"] == "wallet_charge":
                if not complete_payment(payment_id):  # atomic: only one path wins
                    bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
                    return
                update_balance(uid, payment["amount"])
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, f"✅ پرداخت شما تأیید و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان", back_button("main"))
                state_clear(uid)
            else:
                config_id = payment["config_id"]
                package_id = payment["package_id"]
                package_row = get_package(package_id)
                if not config_id:
                    config_id = reserve_first_config(package_id, payment_id)
                if not config_id:
                    pending_id = create_pending_order(uid, package_id, payment_id, payment["amount"], "tetrapay")
                    complete_payment(payment_id)
                    bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                    send_or_edit(call,
                        "✅ پرداخت شما تأیید شد.\n\n"
                        "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                        "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                        "🙏 از صبر شما متشکریم.", back_button("main"))
                    notify_pending_order_to_admins(pending_id, uid, package_row, payment["amount"], "tetrapay")
                    state_clear(uid)
                    return
                purchase_id = assign_config_to_user(config_id, uid, package_id, payment["amount"], "tetrapay", is_test=0)
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, "✅ پرداخت شما تأیید شد و سرویس آماده است.", back_button("main"))
                deliver_purchase_message(call.message.chat.id, purchase_id)
                admin_purchase_notify("TetraPay", get_user(uid), package_row)
                state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("pay:tetrapay:"):
        package_id = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("tetrapay", price):
            _rng = get_gateway_range_text("tetrapay")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای درگاه TetraPay مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        hash_id = f"cfg-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_tetrapay_order(price, hash_id, f"خرید {package_row['name']}")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد درخواست پرداخت آنلاین.", show_alert=True)
            return
        authority = result.get("Authority", "")
        pay_url_bot = result.get("payment_url_bot", "")
        pay_url_web = result.get("payment_url_web", "")
        payment_id = create_payment("config_purchase", uid, package_id, price, "tetrapay", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (authority, payment_id))
        state_set(uid, "await_tetrapay_verify", payment_id=payment_id, authority=authority)
        text = (
            "🏦 <b>پرداخت آنلاین (TetraPay)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n\n"
            "لطفاً از یکی از لینک‌های زیر پرداخت را انجام دهید.\n\n"
            "⏳ <b>تا یک ساعت</b> اگر پرداخت‌تون تایید بشه به صورت خودکار عملیات انجام می‌شود.\n"
            "در غیر این صورت دکمه <b>بررسی پرداخت</b> را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        if pay_url_bot and setting_get("tetrapay_mode_bot", "1") == "1":
            kb.add(types.InlineKeyboardButton("💳 پرداخت در تلگرام", url=pay_url_bot))
        if pay_url_web and setting_get("tetrapay_mode_web", "1") == "1":
            kb.add(types.InlineKeyboardButton("🌐 پرداخت در مرورگر", url=pay_url_web))
        kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=f"pay:tetrapay:verify:{payment_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        _start_tetrapay_auto_verify(
            payment_id, authority, uid,
            call.message.chat.id, call.message.message_id,
            "config_purchase", package_id=package_id)
        return

    # ── TronPays Rial: purchase ───────────────────────────────────────────────
    if data.startswith("pay:tronpays_rial:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        ok, status = check_tronpays_rial_invoice(invoice_id)
        if not ok:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        if is_tronpays_paid(status):
            if payment["kind"] == "wallet_charge":
                if not complete_payment(payment_id):  # atomic: only one path wins
                    bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
                    return
                update_balance(uid, payment["amount"])
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, f"✅ پرداخت شما تأیید و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان",
                             back_button("main"))
                state_clear(uid)
            else:
                config_id  = payment["config_id"]
                package_id = payment["package_id"]
                package_row = get_package(package_id)
                if not config_id:
                    config_id = reserve_first_config(package_id, payment_id)
                if not config_id:
                    pending_id = create_pending_order(uid, package_id, payment_id, payment["amount"], "tronpays_rial")
                    complete_payment(payment_id)
                    bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                    send_or_edit(call,
                        "✅ پرداخت شما تأیید شد.\n\n"
                        "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                        "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                        "🙏 از صبر شما متشکریم.", back_button("main"))
                    notify_pending_order_to_admins(pending_id, uid, package_row, payment["amount"], "tronpays_rial")
                    state_clear(uid)
                    return
                purchase_id = assign_config_to_user(config_id, uid, package_id, payment["amount"], "tronpays_rial", is_test=0)
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, "✅ پرداخت شما تأیید شد و سرویس آماده است.", back_button("main"))
                deliver_purchase_message(call.message.chat.id, purchase_id)
                admin_purchase_notify("TronPays", get_user(uid), package_row)
                state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("pay:tronpays_rial:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price   = get_effective_price(uid, package_row)
        if not is_gateway_in_range("tronpays_rial", price):
            _rng = get_gateway_range_text("tronpays_rial")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای درگاه TronsPay مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        hash_id = f"cfg-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_tronpays_rial_invoice(price, hash_id, f"خرید {package_row['name']}")
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"⚠️ <b>خطا در ایجاد درگاه TronPays</b>\n\n"
                f"<code>{esc(err_msg[:400])}</code>\n\n"
                "💡 مطمئن شوید کلید API صحیح وارد شده باشد.",
                back_button(f"buy:p:{package_id}"))
            return
        invoice_id = result["invoice_id"]
        invoice_url = result["invoice_url"]
        payment_id = create_payment("config_purchase", uid, package_id, price, "tronpays_rial", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_tronpays_rial_verify", payment_id=payment_id, invoice_id=invoice_id)
        text = (
            "💳 <b>پرداخت ریالی (TronPays)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n\n"
            "از لینک زیر پرداخت را انجام دهید.\n\n"
            "⏳ <b>تا یک ساعت</b> پرداخت به صورت خودکار بررسی می‌شود.\n"
            "در غیر این صورت دکمه «بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💳 پرداخت از درگاه TronPays", url=invoice_url))
        kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=f"pay:tronpays_rial:verify:{payment_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        _start_tronpays_rial_auto_verify(
            payment_id, invoice_id, uid,
            call.message.chat.id, call.message.message_id,
            "config_purchase", package_id=package_id)
        return

    # ── Free test ─────────────────────────────────────────────────────────────
    if data == "test:start":
        if setting_get("free_test_enabled", "1") != "1":
            bot.answer_callback_query(call.id, "تست رایگان غیرفعال است.", show_alert=True)
            return
        user = get_user(uid)
        is_agent_user = user and user["is_agent"]
        if is_agent_user:
            agent_limit = int(setting_get("agent_test_limit", "0") or "0")
            agent_period = setting_get("agent_test_period", "day")
            if agent_limit > 0:
                used = agent_test_count_in_period(uid, agent_period)
                if used >= agent_limit:
                    period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
                    bot.answer_callback_query(call.id,
                        f"شما سقف تست رایگان ({agent_limit} عدد در {period_labels.get(agent_period, agent_period)}) را استفاده کرده‌اید.",
                        show_alert=True)
                    return
        else:
            if user_has_any_test(uid):
                bot.answer_callback_query(call.id, "شما قبلاً تست رایگان خود را دریافت کرده‌اید.", show_alert=True)
                return
        items = get_active_types()
        kb    = types.InlineKeyboardMarkup()
        has_any = False
        for item in items:
            packs = [p for p in get_packages(type_id=item['id'], price_only=0) if p['stock'] > 0]
            if packs:
                kb.add(types.InlineKeyboardButton(f"🎁 {item['name']}", callback_data=f"test:t:{item['id']}"))
                has_any = True
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        if not has_any:
            send_or_edit(call, "📭 در حال حاضر تست رایگانی موجود نیست.", kb)
        else:
            send_or_edit(call, "🎁 <b>تست رایگان</b>\n\nنوع مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("test:t:"):
        if setting_get("free_test_enabled", "1") != "1":
            bot.answer_callback_query(call.id, "تست رایگان غیرفعال است.", show_alert=True)
            return
        user = get_user(uid)
        is_agent_user = user and user["is_agent"]
        if is_agent_user:
            agent_limit = int(setting_get("agent_test_limit", "0") or "0")
            agent_period = setting_get("agent_test_period", "day")
            if agent_limit > 0:
                used = agent_test_count_in_period(uid, agent_period)
                if used >= agent_limit:
                    period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
                    bot.answer_callback_query(call.id,
                        f"شما سقف تست رایگان ({agent_limit} عدد در {period_labels.get(agent_period, agent_period)}) را استفاده کرده‌اید.",
                        show_alert=True)
                    return
        else:
            if user_has_any_test(uid):
                bot.answer_callback_query(call.id, "شما قبلاً تست رایگان خود را دریافت کرده‌اید.", show_alert=True)
                return
        type_id     = int(data.split(":")[2])
        type_row    = get_type(type_id)
        package_row = None
        for item in get_packages(type_id=type_id, price_only=0):
            if item["stock"] > 0:
                package_row = item
                break
        if not package_row:
            bot.answer_callback_query(call.id, "برای این نوع تست رایگان موجود نیست.", show_alert=True)
            return
        config_id = reserve_first_config(package_row["id"])
        if not config_id:
            bot.answer_callback_query(call.id, "تست رایگان این نوع تمام شده است.", show_alert=True)
            return
        try:
            purchase_id = assign_config_to_user(config_id, uid, package_row["id"], 0, "free_test", is_test=1)
        except Exception:
            release_reserved_config(config_id)
            bot.answer_callback_query(call.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "تست رایگان ارسال شد.")
        send_or_edit(call, f"✅ تست رایگان نوع <b>{esc(type_row['name'])}</b> آماده شد.", back_button("main"))
        deliver_purchase_message(call.message.chat.id, purchase_id)
        return

    # ── Wallet charge ─────────────────────────────────────────────────────────
    if data == "wallet:charge":
        state_set(uid, "await_wallet_amount")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💳 <b>شارژ کیف پول</b>\n\nمبلغ مورد نظر را به تومان وارد کنید:", kb)
        return

    if data == "wallet:charge:card":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        if not is_gateway_in_range("card", amount):
            _rng = get_gateway_range_text("card")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(amount)} تومان برای این درگاه مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        card  = setting_get("payment_card", "")
        bank  = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        if not card:
            bot.answer_callback_query(call.id, "اطلاعات پرداخت هنوز ثبت نشده است.", show_alert=True)
            return
        payment_id = create_payment("wallet_charge", uid, None, amount, "card", status="pending")
        state_set(uid, "await_wallet_receipt", payment_id=payment_id, amount=amount)
        text = (
            "💳 <b>کارت به کارت</b>\n\n"
            f"لطفاً مبلغ <b>{fmt_price(amount)}</b> تومان را به کارت زیر واریز کنید:\n\n"
            f"🏦 {esc(bank or 'ثبت نشده')}\n"
            f"👤 {esc(owner or 'ثبت نشده')}\n"
            f"💳 <code>{esc(card)}</code>\n\n"
            "📸 پس از واریز، تصویر رسید یا شماره پیگیری را ارسال کنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "wallet:charge:crypto":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        if not is_gateway_in_range("crypto", amount):
            _rng = get_gateway_range_text("crypto")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(amount)} تومان برای این درگاه مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        state_set(uid, "wallet_crypto_select_coin", amount=amount)
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=amount)
        return

    if data == "wallet:charge:tetrapay":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        if not is_gateway_in_range("tetrapay", amount):
            _rng = get_gateway_range_text("tetrapay")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(amount)} تومان برای درگاه TetraPay مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        hash_id = f"wallet-{uid}-{int(datetime.now().timestamp())}"
        success, result = create_tetrapay_order(amount, hash_id, "شارژ کیف پول")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد درخواست پرداخت آنلاین.", show_alert=True)
            return
        authority = result.get("Authority", "")
        pay_url_bot = result.get("payment_url_bot", "")
        pay_url_web = result.get("payment_url_web", "")
        payment_id = create_payment("wallet_charge", uid, None, amount, "tetrapay", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (authority, payment_id))
        state_set(uid, "await_tetrapay_verify", payment_id=payment_id, authority=authority)
        text = (
            "🏦 <b>شارژ کیف پول - پرداخت آنلاین (TetraPay)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان\n\n"
            "لطفاً از یکی از لینک‌های زیر پرداخت را انجام دهید.\n\n"
            "⏳ <b>تا یک ساعت</b> اگر پرداخت‌تون تایید بشه به صورت خودکار کیف پول شارژ می‌شود.\n"
            "در غیر این صورت دکمه <b>بررسی پرداخت</b> را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        if pay_url_bot and setting_get("tetrapay_mode_bot", "1") == "1":
            kb.add(types.InlineKeyboardButton("💳 پرداخت در تلگرام", url=pay_url_bot))
        if pay_url_web and setting_get("tetrapay_mode_web", "1") == "1":
            kb.add(types.InlineKeyboardButton("🌐 پرداخت در مرورگر", url=pay_url_web))
        kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=f"pay:tetrapay:verify:{payment_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        _start_tetrapay_auto_verify(
            payment_id, authority, uid,
            call.message.chat.id, call.message.message_id,
            "wallet_charge")
        return

    # ── SwapWallet Crypto (network selection) ─────────────────────────────────
    if data == "wallet:charge:swapwallet_crypto":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        if not is_gateway_in_range("swapwallet_crypto", amount):
            _rng = get_gateway_range_text("swapwallet_crypto")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(amount)} تومان برای درگاه SwapWallet مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        from ..gateways.swapwallet_crypto import SWAPWALLET_CRYPTO_NETWORKS, NETWORK_LABELS as SW_NET_LABELS
        state_set(uid, "swcrypto_network_select", kind="wallet_charge", amount=amount)
        kb = types.InlineKeyboardMarkup()
        for net, _ in SWAPWALLET_CRYPTO_NETWORKS:
            kb.add(types.InlineKeyboardButton(SW_NET_LABELS.get(net, net), callback_data=f"swcrypto:net:{net}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💎 <b>پرداخت کریپتو (SwapWallet)</b>\n\nشبکه مورد نظر را انتخاب کنید:", kb)
        return

    if data == "wallet:charge:tronpays_rial":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        if not is_gateway_in_range("tronpays_rial", amount):
            _rng = get_gateway_range_text("tronpays_rial")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(amount)} تومان برای درگاه TronsPay مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        order_id = f"wallet-{uid}-{int(datetime.now().timestamp())}"
        success, result = create_tronpays_rial_invoice(amount, order_id, "شارژ کیف پول")
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"⚠️ <b>خطا در ایجاد درگاه TronPays</b>\n\n"
                f"<code>{esc(err_msg[:400])}</code>\n\n"
                "💡 مطمئن شوید کلید API صحیح وارد شده باشد.",
                back_button("wallet:charge"))
            return
        invoice_id = result["invoice_id"]
        invoice_url = result["invoice_url"]
        payment_id = create_payment("wallet_charge", uid, None, amount, "tronpays_rial", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_tronpays_rial_verify", payment_id=payment_id, invoice_id=invoice_id)
        text = (
            "💳 <b>شارژ کیف پول — TronPays</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان\n\n"
            "از لینک زیر پرداخت را انجام دهید.\n\n"
            "⏳ <b>تا یک ساعت</b> پرداخت به صورت خودکار بررسی می‌شود.\n"
            "در غیر این صورت دکمه «بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💳 پرداخت از درگاه TronPays", url=invoice_url))
        kb.add(types.InlineKeyboardButton("🔍 بررسی پرداخت", callback_data=f"pay:tronpays_rial:verify:{payment_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        _start_tronpays_rial_auto_verify(
            payment_id, invoice_id, uid,
            call.message.chat.id, call.message.message_id,
            "wallet_charge")
        return

    if data.startswith("pay:swapwallet_crypto:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        success, inv = check_swapwallet_crypto_invoice(invoice_id)
        if not success:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        inv_status = inv.get("status", "")
        if inv_status in ("PAID", "COMPLETED") or inv.get("paidAt"):
            if payment["kind"] == "wallet_charge":
                if not complete_payment(payment_id):  # atomic: only one path wins
                    bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
                    return
                update_balance(uid, payment["amount"])
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, f"✅ پرداخت شما تأیید و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان",
                             back_button("main"))
                state_clear(uid)
            else:
                config_id  = payment["config_id"]
                package_id = payment["package_id"]
                package_row = get_package(package_id)
                if not config_id:
                    config_id = reserve_first_config(package_id, payment_id)
                if not config_id:
                    pending_id = create_pending_order(uid, package_id, payment_id, payment["amount"], "swapwallet_crypto")
                    complete_payment(payment_id)
                    bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                    send_or_edit(call,
                        "✅ پرداخت شما تأیید شد.\n\n"
                        "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                        "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                        "🙏 از صبر شما متشکریم.", back_button("main"))
                    notify_pending_order_to_admins(pending_id, uid, package_row, payment["amount"], "swapwallet_crypto")
                    state_clear(uid)
                    return
                purchase_id = assign_config_to_user(config_id, uid, package_id, payment["amount"], "swapwallet_crypto", is_test=0)
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, "✅ پرداخت شما تأیید شد و سرویس آماده است.", back_button("main"))
                deliver_purchase_message(call.message.chat.id, purchase_id)
                admin_purchase_notify("SwapWallet Crypto", get_user(uid), package_row)
                state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا واریز را انجام دهید.", show_alert=True)
        return

    if data.startswith("pay:swapwallet_crypto:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("swapwallet_crypto", price):
            _rng = get_gateway_range_text("swapwallet_crypto")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای درگاه SwapWallet مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        from ..gateways.swapwallet_crypto import SWAPWALLET_CRYPTO_NETWORKS, NETWORK_LABELS as SW_NET_LABELS
        state_set(uid, "swcrypto_network_select", kind="config_purchase", package_id=package_id, amount=price)
        kb = types.InlineKeyboardMarkup()
        for net, _ in SWAPWALLET_CRYPTO_NETWORKS:
            kb.add(types.InlineKeyboardButton(SW_NET_LABELS.get(net, net), callback_data=f"swcrypto:net:{net}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"buy:p:{package_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💎 <b>پرداخت کریپتو (SwapWallet)</b>\n\nشبکه مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("rpay:swapwallet_crypto:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        success, inv = check_swapwallet_crypto_invoice(invoice_id)
        if not success:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        if inv.get("status") in ("PAID", "COMPLETED") or inv.get("paidAt"):
            complete_payment(payment_id)
            package_row = get_package(payment["package_id"])
            config_id   = payment["config_id"]
            with get_conn() as conn:
                row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (config_id,)).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item = get_purchase(purchase_id) if purchase_id else None
            bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
            send_or_edit(call,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم.",
                back_button("main"))
            if item:
                admin_renewal_notify(uid, item, package_row, payment["amount"], "SwapWallet Crypto")
            state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا واریز را انجام دهید.", show_alert=True)
        return

    if data.startswith("rpay:swapwallet_crypto:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if not is_gateway_in_range("swapwallet_crypto", price):
            _rng = get_gateway_range_text("swapwallet_crypto")
            bot.answer_callback_query(call.id,
                f"⛔️ مبلغ {fmt_price(price)} تومان برای درگاه SwapWallet مجاز نیست.\n"
                f"محدوده مجاز: {_rng}\n\n"
                "لطفاً درگاه دیگری متناسب با این مبلغ انتخاب کنید.",
                show_alert=True)
            return
        from ..gateways.swapwallet_crypto import SWAPWALLET_CRYPTO_NETWORKS, NETWORK_LABELS as SW_NET_LABELS
        state_set(uid, "swcrypto_network_select", kind="renewal",
                  purchase_id=purchase_id, package_id=package_id,
                  amount=price, config_id=item["config_id"])
        kb = types.InlineKeyboardMarkup()
        for net, _ in SWAPWALLET_CRYPTO_NETWORKS:
            kb.add(types.InlineKeyboardButton(SW_NET_LABELS.get(net, net), callback_data=f"swcrypto:net:{net}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"renew:{purchase_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💎 <b>پرداخت کریپتو (SwapWallet)</b>\n\nشبکه مورد نظر را انتخاب کنید:", kb)
        return

    # ── SwapWallet Crypto: network selected → create invoice ─────────────────
    if data.startswith("swcrypto:net:"):
        network = data.split(":")[2]
        sd      = state_data(uid)
        kind    = sd.get("kind", "")
        amount  = sd.get("amount", 0)
        if not amount:
            bot.answer_callback_query(call.id, "خطا در اطلاعات سفارش.", show_alert=True)
            return
        order_id = f"swc-{uid}-{int(datetime.now().timestamp())}"
        desc = "شارژ کیف پول" if kind == "wallet_charge" else "پرداخت کریپتو"
        success, result = create_swapwallet_crypto_invoice(amount, order_id, network, desc)
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            _swapwallet_error_inline(call, err_msg)
            return
        invoice_id = result.get("id", "")
        if kind == "wallet_charge":
            payment_id = create_payment("wallet_charge", uid, None, amount, "swapwallet_crypto", status="pending")
            verify_cb  = f"pay:swapwallet_crypto:verify:{payment_id}"
        elif kind == "config_purchase":
            package_id = sd.get("package_id")
            payment_id = create_payment("config_purchase", uid, package_id, amount, "swapwallet_crypto", status="pending")
            verify_cb  = f"pay:swapwallet_crypto:verify:{payment_id}"
        elif kind == "renewal":
            package_id  = sd.get("package_id")
            config_id_r = sd.get("config_id")
            payment_id  = create_payment("renewal", uid, package_id, amount, "swapwallet_crypto",
                                          status="pending", config_id=config_id_r)
            verify_cb   = f"rpay:swapwallet_crypto:verify:{payment_id}"
        else:
            bot.answer_callback_query(call.id, "خطا در نوع پرداخت.", show_alert=True)
            return
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_swapwallet_crypto_verify", payment_id=payment_id, invoice_id=invoice_id)
        bot.answer_callback_query(call.id)
        show_swapwallet_crypto_page(call, amount_toman=amount, invoice_id=invoice_id,
                                    result=result, payment_id=payment_id, verify_cb=verify_cb)
        return

    # ── Admin panel ────────────────────────────────────────────────────────────
    if not is_admin(uid):
        # Non-admin shouldn't reach admin callbacks, just ignore
        if data.startswith("admin:") or data.startswith("adm:"):
            bot.answer_callback_query(call.id, "اجازه دسترسی ندارید.", show_alert=True)
            return

    if data == "admin:panel":
        bot.answer_callback_query(call.id)
        text = (
            "⚙️ <b>پنل مدیریت</b>\n\n"
            "بخش مورد نظر را انتخاب کنید:\n\n"
            "────────────────\n"
            "💡 <b>ConfigFlow v2.0</b>\n"
            "👨‍💻 Developer: @Emad_Habibnia\n"
            "🌐 <a href='https://github.com/Emadhabibnia1385/ConfigFlow'>GitHub ConfigFlow</a>\n"
            "❤️ <a href='https://t.me/EmadHabibnia/4'>donate</a>"
        )
        send_or_edit(call, text, kb_admin_panel(uid))
        return

    # ── Admin: Types ──────────────────────────────────────────────────────────
    if data == "admin:types":
        if not admin_has_perm(uid, "types_packages"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        _show_admin_types(call)
        bot.answer_callback_query(call.id)
        return

    if data == "admin:type:add":
        state_set(uid, "admin_add_type")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🧩 نام نوع جدید را ارسال کنید:", back_button("admin:types"))
        return

    if data.startswith("admin:type:edit:"):
        type_id = int(data.split(":")[3])
        row     = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"admin:type:editname:{type_id}"))
        kb.add(types.InlineKeyboardButton("📝 ویرایش توضیحات", callback_data=f"admin:type:editdesc:{type_id}"))
        if row["description"]:
            kb.add(types.InlineKeyboardButton("🗑 حذف توضیحات", callback_data=f"admin:type:deldesc:{type_id}"))
        is_active = row["is_active"] if "is_active" in row.keys() else 1
        status_label = "✅ فعال — کلیک برای غیرفعال" if is_active else "❌ غیرفعال — کلیک برای فعال"
        kb.add(types.InlineKeyboardButton(status_label, callback_data=f"admin:type:toggleactive:{type_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:types"))
        desc_preview = f"\n📝 توضیحات: {esc(row['description'][:80])}..." if row["description"] and len(row["description"]) > 80 else (f"\n📝 توضیحات: {esc(row['description'])}" if row["description"] else "\n📝 توضیحات: ندارد")
        status_line  = "\n🔘 وضعیت: <b>فعال</b>" if is_active else "\n🔘 وضعیت: <b>غیرفعال</b>"
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ <b>ویرایش نوع:</b> {esc(row['name'])}{desc_preview}{status_line}", kb)
        return

    if data.startswith("admin:type:editname:"):
        type_id = int(data.split(":")[3])
        row     = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        state_set(uid, "admin_edit_type", type_id=type_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ نام جدید برای نوع <b>{esc(row['name'])}</b> را ارسال کنید:",
                     back_button("admin:types"))
        return

    if data.startswith("admin:type:editdesc:"):
        type_id = int(data.split(":")[3])
        row     = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        state_set(uid, "admin_edit_type_desc", type_id=type_id)
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏭ توضیحاتی نمی‌خواهم وارد کنم", callback_data=f"admin:type:deldesc:{type_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin:type:edit:{type_id}"))
        send_or_edit(call,
            f"📝 توضیحات جدید برای نوع <b>{esc(row['name'])}</b> را ارسال کنید:\n\n"
            "این توضیحات پس از ارسال کانفیگ به کاربر نمایش داده می‌شود.", kb)
        return

    if data == "admin:type:skipdesc":
        sn = state_name(uid)
        sd_val = state_data(uid)
        if sn == "admin_add_type_desc":
            name = sd_val.get("type_name", "")
            try:
                add_type(name, "")
                state_clear(uid)
                bot.answer_callback_query(call.id, "✅ نوع ثبت شد.")
                bot.send_message(call.message.chat.id, "✅ نوع جدید ثبت شد.", reply_markup=kb_admin_panel())
                log_admin_action(uid, f"نوع جدید ثبت شد: <b>{esc(name)}</b>")
            except sqlite3.IntegrityError:
                state_clear(uid)
                bot.answer_callback_query(call.id, "⚠️ این نوع قبلاً ثبت شده.", show_alert=True)
        else:
            bot.answer_callback_query(call.id)
        return

    if data.startswith("admin:type:deldesc:"):
        type_id = int(data.split(":")[3])
        update_type_description(type_id, "")
        state_clear(uid)
        bot.answer_callback_query(call.id, "✅ توضیحات حذف شد.")
        log_admin_action(uid, f"توضیحات نوع #{type_id} حذف شد")
        _show_admin_types(call)
        return

    if data.startswith("admin:type:toggleactive:"):
        type_id = int(data.split(":")[3])
        row = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        cur = row["is_active"] if "is_active" in row.keys() else 1
        update_type_active(type_id, 0 if cur else 1)
        new_status = "غیرفعال" if cur else "فعال"
        bot.answer_callback_query(call.id, f"✅ نوع {new_status} شد.")
        log_admin_action(uid, f"نوع <b>{esc(row['name'])}</b> {new_status} شد")
        # re-open the edit screen with updated state
        call.data = f"admin:type:edit:{type_id}"
        data      = call.data

    if data.startswith("admin:pkg:toggleactive:"):
        package_id = int(data.split(":")[3])
        pkg = get_package(package_id)
        if not pkg:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        toggle_package_active(package_id)
        cur = pkg["active"] if "active" in pkg.keys() else 1
        new_status = "غیرفعال" if cur else "فعال"
        bot.answer_callback_query(call.id, f"✅ پکیج {new_status} شد.")
        log_admin_action(uid, f"پکیج <b>{esc(pkg['name'])}</b> {new_status} شد")
        call.data = f"admin:pkg:edit:{package_id}"
        data      = call.data

    if data.startswith("admin:type:del:"):
        type_id = int(data.split(":")[3])
        with get_conn() as conn:
            sold_in_type = conn.execute(
                "SELECT COUNT(*) AS n FROM configs c "
                "JOIN packages p ON p.id=c.package_id "
                "WHERE p.type_id=? AND c.sold_to IS NOT NULL",
                (type_id,)
            ).fetchone()["n"]
            if sold_in_type > 0:
                bot.answer_callback_query(call.id, f"❌ {sold_in_type} کانفیگ فروخته‌شده در این نوع وجود دارد.", show_alert=True)
                return
            pack_count = conn.execute(
                "SELECT COUNT(*) AS n FROM packages WHERE type_id=?", (type_id,)
            ).fetchone()["n"]
            total_cfg = conn.execute(
                "SELECT COUNT(*) AS n FROM configs c "
                "JOIN packages p ON p.id=c.package_id WHERE p.type_id=?",
                (type_id,)
            ).fetchone()["n"]
        if pack_count > 0 or total_cfg > 0:
            kb_c = types.InlineKeyboardMarkup()
            kb_c.row(
                types.InlineKeyboardButton("✅ بله، همه حذف شود", callback_data=f"admin:type:delok:{type_id}"),
                types.InlineKeyboardButton("❌ انصراف", callback_data="admin:types"),
            )
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"⚠️ <b>تأیید حذف نوع</b>\n\n"
                f"{pack_count} پکیج و {total_cfg} کانفیگ (موجود/منقضی) همراه با این نوع حذف خواهند شد.\n"
                "آیا مطمئن هستید؟", kb_c)
            return
        delete_type(type_id)
        bot.answer_callback_query(call.id, "✅ نوع حذف شد.")
        log_admin_action(uid, f"نوع #{type_id} حذف شد")
        _show_admin_types(call)
        return

    if data.startswith("admin:type:delok:"):
        type_id = int(data.split(":")[3])
        with get_conn() as conn:
            sold_in_type = conn.execute(
                "SELECT COUNT(*) AS n FROM configs c "
                "JOIN packages p ON p.id=c.package_id "
                "WHERE p.type_id=? AND c.sold_to IS NOT NULL",
                (type_id,)
            ).fetchone()["n"]
        if sold_in_type > 0:
            bot.answer_callback_query(call.id, "❌ در این فاصله کانفیگ فروخته شد. حذف ممکن نیست.", show_alert=True)
            _show_admin_types(call)
            return
        delete_type(type_id)
        bot.answer_callback_query(call.id, "✅ نوع و تمام پکیج‌های آن حذف شدند.")
        log_admin_action(uid, f"نوع #{type_id} با تمام پکیج‌ها حذف شد")
        _show_admin_types(call)
        return

    if data.startswith("admin:pkg:add:t:"):
        type_id  = int(data.split(":")[4])
        type_row = get_type(type_id)
        state_set(uid, "admin_add_package_name", type_id=type_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ نام پکیج برای نوع <b>{esc(type_row['name'])}</b> را وارد کنید:",
                     back_button("admin:types"))
        return

    if data.startswith("admin:pkg:edit:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✏️ ویرایش نام",   callback_data=f"admin:pkg:ef:name:{package_id}"))
        kb.add(types.InlineKeyboardButton("💰 ویرایش قیمت",  callback_data=f"admin:pkg:ef:price:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔋 ویرایش حجم",   callback_data=f"admin:pkg:ef:volume:{package_id}"))
        kb.add(types.InlineKeyboardButton("⏰ ویرایش مدت",   callback_data=f"admin:pkg:ef:dur:{package_id}"))
        kb.add(types.InlineKeyboardButton("📌 جایگاه نمایش",  callback_data=f"admin:pkg:ef:position:{package_id}"))
        pkg_active = package_row['active'] if 'active' in package_row.keys() else 1
        pkg_status_label = "✅ فعال — کلیک برای غیرفعال" if pkg_active else "❌ غیرفعال — کلیک برای فعال"
        kb.add(types.InlineKeyboardButton(pkg_status_label, callback_data=f"admin:pkg:toggleactive:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",       callback_data="admin:types"))
        bot.answer_callback_query(call.id)
        cur_pos = package_row['position'] if 'position' in package_row.keys() else 0
        pkg_status_line = "✅ فعال" if pkg_active else "❌ غیرفعال"
        text = (
            f"📦 <b>ویرایش پکیج</b>\n\n"
            f"نام: {esc(package_row['name'])}\n"
            f"قیمت: {fmt_price(package_row['price'])} تومان\n"
            f"حجم: {fmt_vol(package_row['volume_gb'])}\n"
            f"مدت: {fmt_dur(package_row['duration_days'])}\n"
            f"جایگاه: {cur_pos}\n"
            f"وضعیت: {pkg_status_line}"
        )
        send_or_edit(call, text, kb)
        return

    if data.startswith("admin:pkg:ef:"):
        parts      = data.split(":")
        field_key  = parts[3]
        package_id = int(parts[4])
        state_set(uid, "admin_edit_pkg_field", field_key=field_key, package_id=package_id)
        labels     = {"name": "نام", "price": "قیمت (تومان)", "volume": "حجم (GB)", "dur": "مدت (روز)", "position": "جایگاه نمایش"}
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ مقدار جدید برای <b>{labels.get(field_key, field_key)}</b> را وارد کنید:",
                     back_button("admin:types"))
        return

    if data.startswith("admin:pkg:del:"):
        package_id = int(data.split(":")[3])
        with get_conn() as conn:
            sold_count = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL",
                (package_id,)
            ).fetchone()["n"]
            if sold_count > 0:
                bot.answer_callback_query(call.id, f"❌ این پکیج {sold_count} کانفیگ فروخته‌شده دارد و قابل حذف نیست.", show_alert=True)
                return
            unsold_cfg = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=?",
                (package_id,)
            ).fetchone()["n"]
        if unsold_cfg > 0:
            kb_c = types.InlineKeyboardMarkup()
            kb_c.row(
                types.InlineKeyboardButton("✅ بله، حذف شود", callback_data=f"admin:pkg:delok:{package_id}"),
                types.InlineKeyboardButton("❌ انصراف", callback_data="admin:types"),
            )
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"⚠️ <b>تأیید حذف پکیج</b>\n\n"
                f"{unsold_cfg} کانفیگ موجود/منقضی همراه با پکیج حذف خواهند شد.\n"
                "آیا مطمئن هستید؟", kb_c)
            return
        delete_package(package_id)
        bot.answer_callback_query(call.id, "✅ پکیج حذف شد.")
        log_admin_action(uid, f"پکیج #{package_id} حذف شد")
        _show_admin_types(call)
        return

    if data.startswith("admin:pkg:delok:"):
        package_id = int(data.split(":")[3])
        with get_conn() as conn:
            sold_count = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL",
                (package_id,)
            ).fetchone()["n"]
        if sold_count > 0:
            bot.answer_callback_query(call.id, "❌ در این فاصله کانفیگ فروخته شد. حذف ممکن نیست.", show_alert=True)
            _show_admin_types(call)
            return
        delete_package(package_id)
        bot.answer_callback_query(call.id, "✅ پکیج و کانفیگ‌های آن حذف شدند.")
        log_admin_action(uid, f"پکیج #{package_id} با کانفیگ‌ها حذف شد")
        _show_admin_types(call)
        return

    # ── Admin: Add Config ─────────────────────────────────────────────────────
    if data == "admin:add_config":
        types_list = get_all_types()
        kb = types.InlineKeyboardMarkup()
        for item in types_list:
            kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data=f"adm:cfg:t:{item['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📝 <b>ثبت کانفیگ</b>\n\nنوع کانفیگ را انتخاب کنید:", kb)
        return

    if data.startswith("adm:cfg:t:"):
        type_id = int(data.split(":")[3])
        packs   = get_packages(type_id=type_id)
        kb      = types.InlineKeyboardMarkup()
        for p in packs:
            kb.add(types.InlineKeyboardButton(
                f"{p['name']} | {fmt_vol(p['volume_gb'])} | {fmt_dur(p['duration_days'])}",
                callback_data=f"adm:cfg:p:{p['id']}"
            ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 پکیج مربوطه را انتخاب کنید:", kb)
        return

    if data.startswith("adm:cfg:p:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📝 ثبت تکی", callback_data=f"adm:cfg:single:{package_id}"))
        kb.add(types.InlineKeyboardButton("📋 ثبت دسته‌ای", callback_data=f"adm:cfg:bulk:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:cfg:t:{package_row['type_id']}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📝 روش ثبت کانفیگ را انتخاب کنید:", kb)
        return

    if data.startswith("adm:cfg:single:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        state_set(uid, "admin_add_config_service", package_id=package_id, type_id=package_row["type_id"])
        bot.answer_callback_query(call.id)
        send_or_edit(call, "✏️ نام سرویس را وارد کنید:", back_button("admin:add_config"))
        return

    if data.startswith("adm:cfg:bulk:"):
        # Could be adm:cfg:bulk:{pkg_id} or adm:cfg:bulk:inq:y/n:{pkg_id} or adm:cfg:bulk:skip:...
        rest = data[len("adm:cfg:bulk:"):]

        # Skip prefix
        if rest.startswith("skippre:"):
            pkg_id = int(rest.split(":")[1])
            s = state_data(uid)
            state_set(uid, "admin_bulk_suffix",
                      package_id=s["package_id"], type_id=s["type_id"],
                      has_inquiry=s["has_inquiry"], prefix="")
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                "✂️ <b>پسوند حذفی از نام کانفیگ</b>\n\n"
                "وقتی چندتا اکسترنال پروکسی ست می‌کنید، انتهای نام کانفیگ متن‌های اضافه اکسترنال‌ها اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن‌ها در نام کانفیگ بیاید، پسوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>-main</code>",
                back_button("admin:add_config"))
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ بعدی (بدون پسوند)", callback_data=f"adm:cfg:bulk:skipsuf:{pkg_id}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
            send_or_edit(call,
                "✂️ <b>پسوند حذفی از نام کانفیگ</b>\n\n"
                "وقتی چندتا اکسترنال پروکسی ست می‌کنید، انتهای نام کانفیگ متن‌های اضافه اکسترنال‌ها اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن‌ها در نام کانفیگ بیاید، پسوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>-main</code>", kb)
            return

        # Skip suffix
        if rest.startswith("skipsuf:"):
            pkg_id = int(rest.split(":")[1])
            s = state_data(uid)
            has_inq = s.get("has_inquiry", False)
            prefix = s.get("prefix", "")
            state_set(uid, "admin_bulk_data",
                      package_id=s["package_id"], type_id=s["type_id"],
                      has_inquiry=has_inq, prefix=prefix, suffix="")
            bot.answer_callback_query(call.id)
            if has_inq:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    "کانفیگ‌ها را ارسال کنید. دو روش وجود دارد:\n\n"
                    "<b>📝 روش اول: ارسال متنی</b>\n"
                    "هر کانفیگ <b>دو خط</b> دارد:\n"
                    "خط اول: لینک کانفیگ\n"
                    "خط دوم: لینک استعلام (شروع با http)\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "http://panel.com/sub/1\n"
                    "vless://def...#name2\n"
                    "http://panel.com/sub/2</code>\n\n"
                    "<b>📎 روش دوم: ارسال فایل TXT</b>\n"
                    "اگر تعداد کانفیگ‌هایتان زیاد است (بیش از ۱۰-۱۵ عدد)، "
                    "یک فایل <b>.txt</b> بسازید و تمام لینک‌ها را در آن قرار دهید "
                    "(هر خط یک کانفیگ + خط بعدی لینک استعلام)، سپس فایل را ارسال کنید."
                )
            else:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    "کانفیگ‌ها را ارسال کنید. دو روش وجود دارد:\n\n"
                    "<b>📝 روش اول: ارسال متنی</b>\n"
                    "هر خط یک لینک کانفیگ:\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "vless://def...#name2</code>\n\n"
                    "<b>📎 روش دوم: ارسال فایل TXT</b>\n"
                    "اگر تعداد کانفیگ‌هایتان زیاد است (بیش از ۱۰-۱۵ عدد)، "
                    "یک فایل <b>.txt</b> بسازید و تمام لینک کانفیگ‌ها را در آن قرار دهید "
                    "(هر خط یک کانفیگ)، سپس فایل را ارسال کنید."
                )
            send_or_edit(call, fmt_text, back_button("admin:add_config"))
            return

        # Inquiry yes/no
        if rest.startswith("inq:"):
            sub_parts = rest.split(":")
            yn = sub_parts[1]
            pkg_id = int(sub_parts[2])
            has_inq = (yn == "y")
            state_set(uid, "admin_bulk_prefix",
                      package_id=pkg_id, type_id=state_data(uid).get("type_id", 0),
                      has_inquiry=has_inq)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ بعدی (بدون پیشوند)", callback_data=f"adm:cfg:bulk:skippre:{pkg_id}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                "✂️ <b>پیشوند حذفی از نام کانفیگ</b>\n\n"
                "زمانی که کانفیگ را در پنل می‌سازید، اگر اینباند <b>ریمارک (Remark)</b> دارد، "
                "ابتدای نام کانفیگ اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن در نام کانفیگ بیاید، پیشوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>%E2%9A%95%EF%B8%8FTUN_-</code>\n"
                "یا: <code>⚕️TUN_-</code>", kb)
            return

        # Initial: ask about inquiry links
        package_id  = int(rest)
        package_row = get_package(package_id)
        state_set(uid, "admin_bulk_init", package_id=package_id, type_id=package_row["type_id"])
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ بله", callback_data=f"adm:cfg:bulk:inq:y:{package_id}"),
            types.InlineKeyboardButton("❌ خیر", callback_data=f"adm:cfg:bulk:inq:n:{package_id}"),
        )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:cfg:p:{package_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔗 آیا کانفیگ‌ها <b>لینک استعلام</b> هم دارند؟", kb)
        return

    # ── Admin: Stock / Config management ─────────────────────────────────────
    if data == "admin:stock":
        if not (admin_has_perm(uid, "view_configs") or admin_has_perm(uid, "register_config") or admin_has_perm(uid, "manage_configs")):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        _show_admin_stock(call)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("adm:stk:all:"):
        parts     = data.split(":")
        kind_str  = parts[3]
        page      = int(parts[4])
        # Query all configs across packages
        offset = page * CONFIGS_PER_PAGE
        with get_conn() as conn:
            if kind_str == "sl":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) AS n FROM configs WHERE sold_to IS NOT NULL").fetchone()["n"]
            elif kind_str == "ex":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) AS n FROM configs WHERE is_expired=1").fetchone()["n"]
            else:
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) AS n FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0").fetchone()["n"]
        total_pages = max(1, (total + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE)
        kb         = types.InlineKeyboardMarkup()
        for c in cfgs:
            if c["is_expired"]:
                mark = "❌"
            elif c["sold_to"]:
                mark = "🔴"
            else:
                mark = "🟢"
            svc = urllib.parse.unquote(c["service_name"] or "")
            kb.add(types.InlineKeyboardButton(f"{mark} {svc}", callback_data=f"adm:stk:cfg:{c['id']}"))
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton("⬅️ قبلی", callback_data=f"adm:stk:all:{kind_str}:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton("بعدی ➡️", callback_data=f"adm:stk:all:{kind_str}:{page+1}"))
        if nav_row:
            kb.row(*nav_row)
        # Bulk action buttons
        if kind_str in ("av", "sl"):
            kb.row(
                types.InlineKeyboardButton("🗑 حذف همگانی",   callback_data=f"adm:stk:blkA:{kind_str}"),
                types.InlineKeyboardButton("❌ منقضی همگانی", callback_data=f"adm:stk:blkA:{kind_str}"),
            )
        else:
            kb.add(types.InlineKeyboardButton("🗑 حذف همگانی", callback_data=f"adm:stk:blkA:{kind_str}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        bot.answer_callback_query(call.id)
        if kind_str == "sl":
            label_kind = "🔴 کل فروخته شده"
        elif kind_str == "ex":
            label_kind = "❌ کل منقضی شده"
        else:
            label_kind = "🟢 کل موجود"
        send_or_edit(call, f"📋 {label_kind} | صفحه {page+1}/{total_pages} | تعداد کل: {total}", kb)
        return

    if data.startswith("adm:stk:pk:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        avail = count_configs(package_id, sold=False)
        sold  = count_configs(package_id, sold=True)
        with get_conn() as conn:
            expired = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND is_expired=1",
                (package_id,)
            ).fetchone()["n"]
            pending_c = conn.execute(
                "SELECT COUNT(*) AS n FROM pending_orders WHERE package_id=? AND status='waiting'",
                (package_id,)
            ).fetchone()["n"]
        kb    = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"🟢 مانده ({avail})",       callback_data=f"adm:stk:av:{package_id}:0"),
            types.InlineKeyboardButton(f"🔴 فروخته ({sold})",       callback_data=f"adm:stk:sl:{package_id}:0"),
        )
        kb.add(types.InlineKeyboardButton(f"❌ منقضی ({expired})",  callback_data=f"adm:stk:ex:{package_id}:0"))
        if pending_c > 0:
            kb.add(types.InlineKeyboardButton(
                f"⏳ تحویل {pending_c} سفارش در انتظار",
                callback_data=f"adm:stk:fulfill:{package_id}"
            ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        bot.answer_callback_query(call.id)
        pending_line = f"\n⏳ سفارش در انتظار: {pending_c}" if pending_c > 0 else ""
        text = (
            f"📦 <b>{esc(package_row['name'])}</b>\n\n"
            f"🟢 موجود: {avail}\n"
            f"🔴 فروخته شده: {sold}\n"
            f"❌ منقضی شده: {expired}"
            f"{pending_line}"
        )
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:stk:fulfill:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        bot.answer_callback_query(call.id, "⏳ در حال تحویل سفارش‌ها...")
        try:
            fulfilled = auto_fulfill_pending_orders(package_id)
            if fulfilled > 0:
                send_or_edit(call,
                    f"✅ <b>{fulfilled}</b> سفارش با موفقیت تحویل داده شد.",
                    back_button(f"adm:stk:pk:{package_id}"))
            else:
                # Check if there are still pending orders (no stock available)
                with get_conn() as conn:
                    remaining = conn.execute(
                        "SELECT COUNT(*) AS n FROM pending_orders WHERE package_id=? AND status='waiting'",
                        (package_id,)
                    ).fetchone()["n"]
                if remaining > 0:
                    send_or_edit(call,
                        f"⚠️ <b>{remaining}</b> سفارش در انتظار وجود دارد ولی موجودی کافی نیست.\n\n"
                        "لطفاً ابتدا کانفیگ ثبت کنید.",
                        back_button(f"adm:stk:pk:{package_id}"))
                else:
                    send_or_edit(call,
                        "✅ هیچ سفارش در انتظاری وجود ندارد.",
                        back_button(f"adm:stk:pk:{package_id}"))
        except Exception as e:
            send_or_edit(call,
                f"❌ خطا در تحویل سفارش‌ها:\n<code>{esc(str(e))}</code>",
                back_button(f"adm:stk:pk:{package_id}"))
        return

    if data.startswith("adm:stk:av:") or data.startswith("adm:stk:sl:") or data.startswith("adm:stk:ex:"):
        parts      = data.split(":")
        kind_str   = parts[2]
        package_id = int(parts[3])
        page       = int(parts[4])
        offset     = page * CONFIGS_PER_PAGE
        with get_conn() as conn:
            if kind_str == "sl":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE package_id=? AND sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (package_id, CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL",
                    (package_id,)
                ).fetchone()["n"]
            elif kind_str == "ex":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE package_id=? AND is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (package_id, CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND is_expired=1",
                    (package_id,)
                ).fetchone()["n"]
            else:
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (package_id, CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0",
                    (package_id,)
                ).fetchone()["n"]
        total_pages = max(1, (total + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE)
        kb         = types.InlineKeyboardMarkup()
        for c in cfgs:
            if c["is_expired"]:
                mark = "❌"
            elif c["sold_to"]:
                mark = "🔴"
            else:
                mark = "🟢"
            svc = urllib.parse.unquote(c["service_name"] or "")
            kb.add(types.InlineKeyboardButton(f"{mark} {svc}", callback_data=f"adm:stk:cfg:{c['id']}"))
        # Pagination
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton("⬅️ قبل", callback_data=f"adm:stk:{kind_str}:{package_id}:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton("بعد ➡️", callback_data=f"adm:stk:{kind_str}:{package_id}:{page+1}"))
        if nav_row:
            kb.row(*nav_row)
        # Bulk action buttons
        if kind_str in ("av", "sl"):
            kb.row(
                types.InlineKeyboardButton("🗑 حذف همگانی",   callback_data=f"adm:stk:blk:{kind_str}:{package_id}"),
                types.InlineKeyboardButton("❌ منقضی همگانی", callback_data=f"adm:stk:blk:{kind_str}:{package_id}"),
            )
        else:
            kb.add(types.InlineKeyboardButton("🗑 حذف همگانی", callback_data=f"adm:stk:blk:{kind_str}:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:stk:pk:{package_id}"))
        bot.answer_callback_query(call.id)
        if kind_str == "sl":
            label_kind = "🔴 فروخته شده"
        elif kind_str == "ex":
            label_kind = "❌ منقضی شده"
        else:
            label_kind = "🟢 موجود"
        send_or_edit(call, f"📋 {label_kind} | صفحه {page+1}/{total_pages} | تعداد کل: {total}", kb)
        return

    if data.startswith("adm:stk:cfg:"):
        config_id = int(data.split(":")[3])
        with get_conn() as conn:
            row = conn.execute(
                """SELECT c.*, p.name AS pkg_name, p.volume_gb, p.duration_days, t.name AS type_name
                   FROM configs c
                   JOIN packages p ON p.id=c.package_id
                   JOIN config_types t ON t.id=c.type_id
                   WHERE c.id=?""",
                (config_id,)
            ).fetchone()
        if not row:
            bot.answer_callback_query(call.id, "یافت نشد.", show_alert=True)
            return
        text = (
            f"🔮 نام سرویس: <b>{esc(urllib.parse.unquote(row['service_name'] or ''))}</b>\n"
            f"🧩 نوع سرویس: {esc(row['type_name'])}\n"
            f"🔋 حجم: {fmt_vol(row['volume_gb'])}\n"
            f"⏰ مدت: {fmt_dur(row['duration_days'])}\n\n"
            f"💝 Config:\n<code>{esc(row['config_text'])}</code>\n\n"
            f"🔋 Subscription: {esc(row['inquiry_link'] or '-')}\n"
            f"🗓 ثبت: {esc(row['created_at'])}"
        )
        kb = types.InlineKeyboardMarkup()
        if row["sold_to"]:
            buyer = get_user_detail(row["sold_to"])
            if buyer:
                text += (
                    f"\n\n🛒 <b>خریدار:</b>\n"
                    f"نام: {esc(buyer['full_name'])}\n"
                    f"نام کاربری: {esc(display_username(buyer['username']))}\n"
                    f"آیدی: <code>{buyer['user_id']}</code>\n"
                    f"زمان خرید: {esc(row['sold_at'] or '-')}"
                )
        if not row["is_expired"]:
            kb.add(types.InlineKeyboardButton("❌ منقضی کردن", callback_data=f"adm:stk:exp:{config_id}:{row['package_id']}"))
        else:
            text += "\n\n⚠️ این سرویس منقضی شده است."
        kb.row(
            types.InlineKeyboardButton("✏️ ویرایش", callback_data=f"adm:stk:edt:{config_id}"),
            types.InlineKeyboardButton("🗑 حذف کانفیگ", callback_data=f"adm:stk:del:{config_id}:{row['package_id']}"),
        )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:stk:pk:{row['package_id']}"))
        bot.answer_callback_query(call.id)
        # Send with QR code
        try:
            qr_img = qrcode.make(row['config_text'])
            bio = io.BytesIO()
            qr_img.save(bio, format="PNG")
            bio.seek(0)
            bio.name = "qrcode.png"
            chat_id = call.message.chat.id
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            bot.send_photo(chat_id, bio, caption=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            send_or_edit(call, text, kb)
        return

    if data.startswith("adm:stk:edt:"):
        parts = data.split(":")
        # adm:stk:edt:{config_id}                 → edit menu
        # adm:stk:edt:pkg:{config_id}             → choose type for package edit
        # adm:stk:edt:pkgt:{config_id}:{type_id}  → choose package within type
        # adm:stk:edt:pkgp:{config_id}:{pkg_id}   → confirm package change
        # adm:stk:edt:svc:{config_id}             → edit service name
        # adm:stk:edt:cfg:{config_id}             → edit config text
        # adm:stk:edt:inq:{config_id}             → edit inquiry link

        sub = parts[3] if len(parts) > 3 else ""

        if sub == "pkg":
            config_id  = int(parts[4])
            types_list = get_all_types()
            kb = types.InlineKeyboardMarkup()
            for t in types_list:
                kb.add(types.InlineKeyboardButton(
                    esc(t["name"]),
                    callback_data=f"adm:stk:edt:pkgt:{config_id}:{t['id']}"
                ))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:stk:edt:{config_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, "🧩 نوع سرویس را انتخاب کنید:", kb)
            return

        if sub == "pkgt":
            config_id = int(parts[4])
            type_id   = int(parts[5])
            pkgs = get_packages(type_id)
            kb = types.InlineKeyboardMarkup()
            for p in pkgs:
                label = f"{esc(p['name'])} | {fmt_vol(p['volume_gb'])} | {fmt_dur(p['duration_days'])}"
                kb.add(types.InlineKeyboardButton(
                    label,
                    callback_data=f"adm:stk:edt:pkgp:{config_id}:{p['id']}"
                ))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:stk:edt:pkg:{config_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, "📦 پکیج را انتخاب کنید:", kb)
            return

        if sub == "pkgp":
            config_id  = int(parts[4])
            package_id = int(parts[5])
            pkg = get_package(package_id)
            update_config_field(config_id, "package_id", package_id)
            if pkg:
                update_config_field(config_id, "type_id", pkg["type_id"])
            log_admin_action(uid, f"پکیج کانفیگ #{config_id} به #{package_id} تغییر کرد")
            bot.answer_callback_query(call.id, "✅ پکیج تغییر کرد.")
            _fake_call(call, f"adm:stk:cfg:{config_id}")
            return

        if sub == "svc":
            config_id = int(parts[4])
            state_set(uid, "admin_cfg_edit_svc", config_id=config_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call, "✏️ نام سرویس جدید را ارسال کنید:", back_button(f"adm:stk:edt:{config_id}"))
            return

        if sub == "cfg":
            config_id = int(parts[4])
            state_set(uid, "admin_cfg_edit_text", config_id=config_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call, "💝 متن کانفیگ جدید را ارسال کنید:", back_button(f"adm:stk:edt:{config_id}"))
            return

        if sub == "inq":
            config_id = int(parts[4])
            state_set(uid, "admin_cfg_edit_inq", config_id=config_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                "🔗 لینک استعلام جدید را ارسال کنید.\n"
                "برای حذف لینک، <code>-</code> بفرستید.",
                back_button(f"adm:stk:edt:{config_id}"))
            return

        # Default: show edit menu
        config_id = int(sub)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📦 ویرایش پکیج",         callback_data=f"adm:stk:edt:pkg:{config_id}"))
        kb.add(types.InlineKeyboardButton("🔮 ویرایش نام سرویس",    callback_data=f"adm:stk:edt:svc:{config_id}"))
        kb.add(types.InlineKeyboardButton("💝 ویرایش متن کانفیگ",   callback_data=f"adm:stk:edt:cfg:{config_id}"))
        kb.add(types.InlineKeyboardButton("🔗 ویرایش لینک استعلام", callback_data=f"adm:stk:edt:inq:{config_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",               callback_data=f"adm:stk:cfg:{config_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "✏️ <b>ویرایش کانفیگ</b>\n\nچه چیزی را ویرایش می‌کنید؟", kb)
        return

    if data.startswith("adm:stk:exp:"):
        parts = data.split(":")
        config_id  = int(parts[3])
        package_id = int(parts[4]) if len(parts) > 4 else 0
        # Notify buyer if any
        with get_conn() as conn:
            row = conn.execute("SELECT sold_to FROM configs WHERE id=?", (config_id,)).fetchone()
        if row and row["sold_to"]:
            try:
                bot.send_message(
                    row["sold_to"],
                    "⚠️ یکی از سرویس‌های شما توسط ادمین منقضی اعلام شده است.\nبرای تمدید با پشتیبانی تماس بگیرید."
                )
            except Exception:
                pass
        bot.answer_callback_query(call.id, "سرویس منقضی شد.")
        back = back_button(f"adm:stk:pk:{package_id}") if package_id else back_button("admin:stock")
        send_or_edit(call, "✅ سرویس منقضی اعلام شد.", back)
        return

    if data.startswith("adm:stk:del:"):
        parts = data.split(":")
        config_id  = int(parts[3])
        package_id = int(parts[4]) if len(parts) > 4 else 0
        with get_conn() as conn:
            conn.execute("DELETE FROM configs WHERE id=?", (config_id,))
        bot.answer_callback_query(call.id, "کانفیگ حذف شد.")
        back = back_button(f"adm:stk:pk:{package_id}") if package_id else back_button("admin:stock")
        send_or_edit(call, "✅ کانفیگ با موفقیت حذف شد.", back)
        return

    # ── Admin: Bulk select — All packages entry (must be before blk: check) ──
    if data.startswith("adm:stk:blkA:"):
        kind = data.split(":")[3]  # av / sl / ex
        if not (admin_has_perm(uid, "manage_configs") or uid in ADMIN_IDS):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "stk_bulk", kind=kind, scope="all", pkg_id=0, page=0, selected="")
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Per-package entry ────────────────────────────────
    if data.startswith("adm:stk:blk:"):
        parts  = data.split(":")
        kind   = parts[3]         # av / sl / ex
        pkg_id = int(parts[4])    # package_id
        if not (admin_has_perm(uid, "manage_configs") or uid in ADMIN_IDS):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "stk_bulk", kind=kind, scope="pk", pkg_id=pkg_id, page=0, selected="")
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Toggle individual config ─────────────────────────
    if data.startswith("adm:stk:btog:"):
        cfg_id   = int(data.split(":")[3])
        sd       = state_data(uid)
        sel_raw  = sd.get("selected", "")
        selected = set(int(x) for x in sel_raw.split(",") if x.strip().lstrip("-").isdigit())
        if cfg_id in selected:
            selected.discard(cfg_id)
        else:
            selected.add(cfg_id)
        state_set(uid, "stk_bulk",
                  kind=sd.get("kind", "av"), scope=sd.get("scope", "pk"),
                  pkg_id=sd.get("pkg_id", 0), page=sd.get("page", 0),
                  selected=",".join(str(x) for x in selected))
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Select all on current page ───────────────────────
    if data == "adm:stk:bsall":
        sd       = state_data(uid)
        sel_raw  = sd.get("selected", "")
        selected = set(int(x) for x in sel_raw.split(",") if x.strip().lstrip("-").isdigit())
        selected.update(_get_bulk_page_ids(sd))
        state_set(uid, "stk_bulk",
                  kind=sd.get("kind", "av"), scope=sd.get("scope", "pk"),
                  pkg_id=sd.get("pkg_id", 0), page=sd.get("page", 0),
                  selected=",".join(str(x) for x in selected))
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Deselect current page ────────────────────────────
    if data == "adm:stk:bclr":
        sd       = state_data(uid)
        sel_raw  = sd.get("selected", "")
        selected = set(int(x) for x in sel_raw.split(",") if x.strip().lstrip("-").isdigit())
        for cid in _get_bulk_page_ids(sd):
            selected.discard(cid)
        state_set(uid, "stk_bulk",
                  kind=sd.get("kind", "av"), scope=sd.get("scope", "pk"),
                  pkg_id=sd.get("pkg_id", 0), page=sd.get("page", 0),
                  selected=",".join(str(x) for x in selected))
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Clear all selections ─────────────────────────────
    if data == "adm:stk:bclrall":
        sd = state_data(uid)
        state_set(uid, "stk_bulk",
                  kind=sd.get("kind", "av"), scope=sd.get("scope", "pk"),
                  pkg_id=sd.get("pkg_id", 0), page=sd.get("page", 0),
                  selected="")
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Navigate pages ───────────────────────────────────
    if data.startswith("adm:stk:bnav:"):
        new_page = int(data.split(":")[3])
        sd = state_data(uid)
        state_set(uid, "stk_bulk",
                  kind=sd.get("kind", "av"), scope=sd.get("scope", "pk"),
                  pkg_id=sd.get("pkg_id", 0), page=new_page,
                  selected=sd.get("selected", ""))
        bot.answer_callback_query(call.id)
        _render_bulk_page(call, uid)
        return

    # ── Admin: Bulk select — Execute delete ───────────────────────────────────
    if data == "adm:stk:bdel":
        sd      = state_data(uid)
        sel_raw = sd.get("selected", "")
        ids     = [int(x) for x in sel_raw.split(",") if x.strip().lstrip("-").isdigit()]
        if not ids:
            bot.answer_callback_query(call.id, "⚠️ هیچ موردی انتخاب نشده.", show_alert=True)
            return
        with get_conn() as conn:
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM configs WHERE id IN ({placeholders})", ids)
        state_clear(uid)
        bot.answer_callback_query(call.id, f"✅ {len(ids)} کانفیگ حذف شد.", show_alert=True)
        send_or_edit(call, f"✅ <b>{len(ids)}</b> کانفیگ با موفقیت حذف شد.", back_button("admin:stock"))
        return

    # ── Admin: Bulk select — Execute expire ───────────────────────────────────
    if data == "adm:stk:bexp":
        sd      = state_data(uid)
        sel_raw = sd.get("selected", "")
        ids     = [int(x) for x in sel_raw.split(",") if x.strip().lstrip("-").isdigit()]
        if not ids:
            bot.answer_callback_query(call.id, "⚠️ هیچ موردی انتخاب نشده.", show_alert=True)
            return
        with get_conn() as conn:
            for cfg_id in ids:
                conn.execute("UPDATE configs SET is_expired=1 WHERE id=?", (cfg_id,))
        state_clear(uid)
        bot.answer_callback_query(call.id, f"✅ {len(ids)} کانفیگ منقضی شد.", show_alert=True)
        send_or_edit(call, f"✅ <b>{len(ids)}</b> کانفیگ منقضی اعلام شد.", back_button("admin:stock"))
        return

    # ── Admin: Bulk select — Cancel / back ────────────────────────────────────
    if data == "adm:stk:bcanc":
        sd     = state_data(uid)
        kind   = sd.get("kind", "av")
        scope  = sd.get("scope", "pk")
        pkg_id = int(sd.get("pkg_id", 0))
        state_clear(uid)
        bot.answer_callback_query(call.id)
        if scope == "pk":
            _fake_call(call, f"adm:stk:{kind}:{pkg_id}:0")
        else:
            _fake_call(call, f"adm:stk:all:{kind}:0")
        return

    # ── Admin: Stock Search ───────────────────────────────────────────────────
    if data == "adm:stk:search":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔗 لینک استعلام", callback_data="adm:stk:srch:link"))
        kb.add(types.InlineKeyboardButton("💝 متن کانفیگ", callback_data="adm:stk:srch:cfg"))
        kb.add(types.InlineKeyboardButton("🔮 نام سرویس", callback_data="adm:stk:srch:name"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        send_or_edit(call, "🔍 جستجو بر اساس:", kb)
        bot.answer_callback_query(call.id)
        return

    if data == "adm:stk:srch:link":
        state_set(call.from_user.id, "admin_search_by_link")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔗 لینک استعلام (یا بخشی از آن) را ارسال کنید:", back_button("adm:stk:search"))
        return

    if data == "adm:stk:srch:cfg":
        state_set(call.from_user.id, "admin_search_by_config")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💝 متن کانفیگ (یا بخشی از آن) را ارسال کنید:", back_button("adm:stk:search"))
        return

    if data == "adm:stk:srch:name":
        state_set(call.from_user.id, "admin_search_by_name")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔮 نام سرویس (یا بخشی از آن) را ارسال کنید:", back_button("adm:stk:search"))
        return

    # ── Admin: Users ──────────────────────────────────────────────────────────
    if data == "admin:users":
        if not (admin_has_perm(uid, "view_users") or admin_has_perm(uid, "full_users") or
                any(admin_has_perm(uid, p) for p in PERM_USER_FULL)):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        _show_admin_users_list(call)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("admin:users:pg:"):
        if not (admin_has_perm(uid, "view_users") or admin_has_perm(uid, "full_users") or
                any(admin_has_perm(uid, p) for p in PERM_USER_FULL)):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        page = int(data.split(":")[-1])
        _show_admin_users_list(call, page=page)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("adm:usr:fl:"):
        if not (admin_has_perm(uid, "view_users") or admin_has_perm(uid, "full_users") or
                any(admin_has_perm(uid, p) for p in PERM_USER_FULL)):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        parts       = data.split(":")
        filter_mode = parts[3]
        page        = int(parts[4]) if len(parts) > 4 else 0
        _show_admin_users_list(call, page=page, filter_mode=filter_mode)
        bot.answer_callback_query(call.id)
        return

    # ── Admin: User search ────────────────────────────────────────────────────
    if data == "adm:usr:search":
        state_set(uid, "admin_user_search")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔍 <b>جستجوی کاربر</b>\n\n"
            "می‌توانید بر اساس موارد زیر جستجو کنید:\n"
            "• <b>آیدی عددی</b> (مثال: <code>123456789</code>)\n"
            "• <b>نام کاربری</b> (مثال: <code>@username</code>)\n"
            "• <b>نام اکانت</b> (مثال: <code>علی</code>)\n\n"
            "مقدار جستجو را ارسال کنید:",
            back_button("admin:users"))
        return

    # ── Admin: Admins management ──────────────────────────────────────────────
    if data == "admin:admins":
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "فقط اونر می‌تواند ادمین‌ها را مدیریت کند.", show_alert=True)
            return
        _show_admin_admins_panel(call)
        bot.answer_callback_query(call.id)
        return

    if data == "adm:mgr:add":
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_mgr_await_id")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "➕ <b>افزودن ادمین جدید</b>\n\n"
            "آیدی عددی یا یوزرنیم کاربر مورد نظر را ارسال کنید:\n\n"
            "مثال: <code>123456789</code> یا <code>@username</code>",
            back_button("admin:admins"))
        return

    if data.startswith("adm:mgr:del:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_id = int(data.split(":")[3])
        if target_id in ADMIN_IDS:
            bot.answer_callback_query(call.id, "اونرها را نمی‌توان حذف کرد.", show_alert=True)
            return
        remove_admin_user(target_id)
        bot.answer_callback_query(call.id, "✅ ادمین حذف شد.")
        log_admin_action(uid, f"ادمین <code>{target_id}</code> حذف شد")
        _show_admin_admins_panel(call)
        return

    if data.startswith("adm:mgr:v:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_id = int(data.split(":")[3])
        row = get_admin_user(target_id)
        user_row = get_user(target_id)
        if not row:
            bot.answer_callback_query(call.id, "ادمین یافت نشد.", show_alert=True)
            return
        perms = json.loads(row["permissions"] or "{}")
        perm_lines = "\n".join(
            f"{'✅' if perms.get(k) or perms.get('full') else '☐'} {lbl}"
            for k, lbl in ADMIN_PERMS if k != "full"
        )
        name = user_row["full_name"] if user_row else f"کاربر {target_id}"
        text = (
            f"👮 <b>اطلاعات ادمین</b>\n\n"
            f"👤 نام: {esc(name)}\n"
            f"🆔 آیدی: <code>{target_id}</code>\n"
            f"📅 افزوده شده: {esc(row['added_at'])}\n\n"
            f"🔑 <b>دسترسی‌ها:</b>\n{perm_lines}"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🗑 حذف ادمین", callback_data=f"adm:mgr:del:{target_id}"))
        kb.add(types.InlineKeyboardButton("✏️ ویرایش دسترسی‌ها", callback_data=f"adm:mgr:edit:{target_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:admins"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:mgr:edit:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_id = int(data.split(":")[3])
        row = get_admin_user(target_id)
        if not row:
            bot.answer_callback_query(call.id, "ادمین یافت نشد.", show_alert=True)
            return
        perms = json.loads(row["permissions"] or "{}")
        state_set(uid, "admin_mgr_select_perms", target_user_id=target_id, perms=json.dumps(perms), edit_mode=True)
        bot.answer_callback_query(call.id)
        _show_perm_selection(call, uid, target_id, perms, edit_mode=True)
        return

    if data.startswith("adm:mgr:pt:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        perm_key = data[len("adm:mgr:pt:"):]
        sd2 = state_data(uid)
        if state_name(uid) != "admin_mgr_select_perms" or not sd2:
            bot.answer_callback_query(call.id, "جلسه منقضی شده است.", show_alert=True)
            return
        target_id = sd2.get("target_user_id")
        perms = json.loads(sd2.get("perms", "{}"))
        current = bool(perms.get(perm_key))

        if perm_key == "full":
            if not current:
                perms = {k: True for k, _ in ADMIN_PERMS}
            else:
                perms = {}
        elif perm_key == "full_users":
            if not current:
                perms["full_users"] = True
                perms["view_users"] = False
                for p in PERM_USER_FULL:
                    perms[p] = True
            else:
                perms["full_users"] = False
                for p in PERM_USER_FULL:
                    perms[p] = False
        elif perm_key == "view_users":
            if not current:
                perms["view_users"] = True
                perms["full_users"] = False
                for p in PERM_USER_FULL:
                    perms[p] = False
            else:
                perms["view_users"] = False
        else:
            perms[perm_key] = not current
            if perm_key in PERM_USER_FULL and perms.get(perm_key):
                perms["view_users"] = False
            if all(perms.get(p) for p in PERM_USER_FULL):
                perms["full_users"] = True
                perms["view_users"] = False
            if all(perms.get(k) for k, _ in ADMIN_PERMS if k != "full"):
                perms["full"] = True

        edit_mode = sd2.get("edit_mode", False)
        state_set(uid, "admin_mgr_select_perms",
                  target_user_id=target_id, perms=json.dumps(perms), edit_mode=edit_mode)
        bot.answer_callback_query(call.id)
        _show_perm_selection(call, uid, target_id, perms, edit_mode=edit_mode)
        return

    if data == "adm:mgr:confirm":
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        sd2 = state_data(uid)
        if state_name(uid) != "admin_mgr_select_perms" or not sd2:
            bot.answer_callback_query(call.id, "جلسه منقضی شده است.", show_alert=True)
            return
        target_id = sd2.get("target_user_id")
        perms = json.loads(sd2.get("perms", "{}"))
        if not any(perms.values()):
            bot.answer_callback_query(call.id, "حداقل یک سطح دسترسی انتخاب کنید.", show_alert=True)
            return
        edit_mode = sd2.get("edit_mode", False)
        # Build human-readable permission list for notification
        perms_labels = {k: v for k, v in ADMIN_PERMS}
        active_perm_names = [perms_labels.get(k, k) for k, v in perms.items() if v]
        perm_text = "\n".join(f"• {p}" for p in active_perm_names) or "— بدون دسترسی —"
        if edit_mode:
            update_admin_permissions(target_id, perms)
            log_admin_action(uid, f"دسترسی‌های ادمین {target_id} به‌روزرسانی شد")
            state_clear(uid)
            bot.answer_callback_query(call.id, "✅ دسترسی‌ها به‌روز شد.")
            try:
                bot.send_message(target_id,
                    "🔑 <b>دسترسی‌های شما به‌روزرسانی شد</b>\n\n"
                    f"<b>دسترسی‌های فعال:</b>\n{perm_text}\n\n"
                    "برای استفاده از دسترسی‌های جدید از /start استفاده کنید.")
            except Exception:
                pass
        else:
            add_admin_user(target_id, uid, perms)
            log_admin_action(uid, f"ادمین جدید {target_id} اضافه شد")
            state_clear(uid)
            bot.answer_callback_query(call.id, "✅ ادمین اضافه شد.")
            try:
                bot.send_message(target_id,
                    "👮 <b>شما به عنوان ادمین اضافه شدید!</b>\n\n"
                    f"<b>دسترسی‌های شما:</b>\n{perm_text}\n\n"
                    "برای دسترسی به پنل مدیریت از دستور /start استفاده کنید.")
            except Exception:
                pass
        _show_admin_admins_panel(call)
        return

    if data.startswith("adm:usr:"):
        parts     = data.split(":")
        sub       = parts[2]
        target_id = int(parts[3]) if len(parts) > 3 else 0

        if sub == "v":   # view user
            _show_admin_user_detail(call, target_id)
            bot.answer_callback_query(call.id)
            return

        if sub == "sts":  # cycle status: safe → unsafe → restricted → safe
            user = get_user(target_id)
            current = user["status"] if user else "safe"
            if current == "safe":
                new_status = "unsafe"
                label = "ناامن"
            elif current == "unsafe":
                new_status = "restricted"
                label = "محدود"
            else:
                new_status = "safe"
                label = "امن"
            set_user_status(target_id, new_status)
            bot.answer_callback_query(call.id, f"وضعیت کاربر به {label} تغییر کرد.")
            log_admin_action(uid, f"وضعیت کاربر <code>{target_id}</code> به {label} تغییر کرد")
            _show_admin_user_detail(call, target_id)
            return

        if sub == "ag":  # toggle agent
            user     = get_user(target_id)
            new_flag = 0 if user["is_agent"] else 1
            set_user_agent(target_id, new_flag)
            label = "فعال" if new_flag else "غیرفعال"
            bot.answer_callback_query(call.id, f"نمایندگی {label} شد.")
            log_admin_action(uid, f"نمایندگی کاربر <code>{target_id}</code> {label} شد")
            _show_admin_user_detail(call, target_id)
            return

        if sub == "bal":  # balance menu
            user = get_user(target_id)
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("➕ افزایش", callback_data=f"adm:usr:bal+:{target_id}"),
                types.InlineKeyboardButton("➖ کاهش",  callback_data=f"adm:usr:bal-:{target_id}"),
            )
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"💰 <b>موجودی کاربر</b>\n\n"
                f"💰 موجودی فعلی: <b>{fmt_price(user['balance'])}</b> تومان",
                kb)
            return

        if sub == "bal+":  # add balance
            state_set(uid, "admin_bal_add", target_user_id=target_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call, f"💰 مبلغی که می‌خواهید <b>اضافه</b> شود را به تومان وارد کنید:",
                         back_button(f"adm:usr:v:{target_id}"))
            return

        if sub == "bal-":  # reduce balance
            state_set(uid, "admin_bal_sub", target_user_id=target_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call, f"💰 مبلغی که می‌خواهید <b>کاهش</b> یابد را به تومان وارد کنید:",
                         back_button(f"adm:usr:v:{target_id}"))
            return

        if sub == "cfgs":  # user configs
            purchases = get_user_purchases(target_id)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("➕ افزودن کانفیگ", callback_data=f"adm:usr:acfg:{target_id}"))
            if purchases:
                for p in purchases:
                    expired_mark = " ❌" if p["is_expired"] else ""
                    svc = urllib.parse.unquote(p["service_name"] or "")
                    kb.add(types.InlineKeyboardButton(
                        f"{svc}{expired_mark}",
                        callback_data=f"adm:usrcfg:{target_id}:{p['config_id']}"
                    ))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, f"📦 کانفیگ‌های کاربر:", kb)
            return

        if sub == "acfg":  # assign config to user
            _show_admin_assign_config_type(call, target_id)
            bot.answer_callback_query(call.id)
            return

        if sub == "agp":  # agency prices list
            packs = get_packages()
            if not packs:
                bot.answer_callback_query(call.id, "پکیجی موجود نیست.", show_alert=True)
                return
            kb = types.InlineKeyboardMarkup()
            for p in packs:
                ap    = get_agency_price(target_id, p["id"])
                price = fmt_price(ap) if ap is not None else fmt_price(p["price"])
                label = f"{p['name']} | {price} ت"
                kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:usr:agpe:{target_id}:{p['id']}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, "🏷 <b>قیمت‌های اختصاصی نمایندگی</b>\n\nبرای ویرایش روی پکیج بزنید:", kb)
            return

    if data.startswith("adm:usr:agpe:"):
        parts      = data.split(":")
        target_id  = int(parts[3])
        package_id = int(parts[4])
        state_set(uid, "admin_set_agency_price", target_user_id=target_id, package_id=package_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💰 قیمت اختصاصی (تومان) را وارد کنید.\nبرای بازگشت به قیمت عادی، عدد <b>0</b> بفرستید:",
                     back_button(f"adm:usr:v:{target_id}"))
        return

    # Admin user config detail (with unassign/delete)
    if data.startswith("adm:usrcfg:"):
        parts     = data.split(":")
        target_id = int(parts[2])
        config_id = int(parts[3])
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM configs WHERE id=?", (config_id,)).fetchone()
        if not row:
            bot.answer_callback_query(call.id, "یافت نشد.", show_alert=True)
            return
        text = (
            f"🔮 نام سرویس: <b>{esc(urllib.parse.unquote(row['service_name'] or ''))}</b>\n\n"
            f"💝 Config:\n<code>{esc(row['config_text'])}</code>\n\n"
            f"🔋 Volume web: {esc(row['inquiry_link'] or '-')}\n"
            f"🗓 ثبت: {esc(row['created_at'])}\n"
            f"🗓 فروش: {esc(row['sold_at'] or '-')}"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔄 حذف از کاربر (برگشت به مانده‌ها)", callback_data=f"adm:usrcfg:unassign:{target_id}:{config_id}"))
        if not row["is_expired"]:
            kb.add(types.InlineKeyboardButton("🔴 منقضی کردن", callback_data=f"adm:stk:exp:{config_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:cfgs:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:usrcfg:unassign:"):
        parts     = data.split(":")
        target_id = int(parts[3])
        config_id = int(parts[4])
        with get_conn() as conn:
            # Reset config to available
            conn.execute("UPDATE configs SET sold_to=NULL, purchase_id=NULL, sold_at=NULL, reserved_payment_id=NULL, is_expired=0 WHERE id=?", (config_id,))
            # Delete the purchase record
            conn.execute("DELETE FROM purchases WHERE config_id=? AND user_id=?", (config_id, target_id))
        bot.answer_callback_query(call.id, "کانفیگ از کاربر حذف شد.")
        send_or_edit(call, "✅ کانفیگ از کاربر حذف و به مانده‌ها برگشت.", back_button(f"adm:usr:v:{target_id}"))
        return

    if data.startswith("adm:acfg:t:"):  # assign config: type selected
        parts     = data.split(":")
        target_id = int(parts[3])
        type_id   = int(parts[4])
        packs     = get_packages(type_id=type_id)
        kb        = types.InlineKeyboardMarkup()
        for p in packs:
            avail = len(get_available_configs_for_package(p["id"]))
            if avail > 0:
                kb.add(types.InlineKeyboardButton(
                    f"{p['name']} | موجود: {avail}",
                    callback_data=f"adm:acfg:p:{target_id}:{p['id']}"
                ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 پکیج مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("adm:acfg:p:"):  # assign config: package selected
        parts      = data.split(":")
        target_id  = int(parts[3])
        package_id = int(parts[4])
        cfgs       = get_available_configs_for_package(package_id)
        kb         = types.InlineKeyboardMarkup()
        for c in cfgs[:50]:
            svc = urllib.parse.unquote(c["service_name"] or "")
            kb.add(types.InlineKeyboardButton(svc,
                                              callback_data=f"adm:acfg:do:{target_id}:{c['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔧 کانفیگ مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("adm:acfg:do:"):  # do assign config
        parts      = data.split(":")
        target_id  = int(parts[3])
        config_id  = int(parts[4])
        with get_conn() as conn:
            cfg_row = conn.execute("SELECT * FROM configs WHERE id=?", (config_id,)).fetchone()
        if not cfg_row:
            bot.answer_callback_query(call.id, "کانفیگ یافت نشد.", show_alert=True)
            return
        purchase_id = assign_config_to_user(config_id, target_id, cfg_row["package_id"], 0, "admin_gift", is_test=0)
        bot.answer_callback_query(call.id, "کانفیگ منتقل شد!")
        send_or_edit(call, "✅ کانفیگ با موفقیت به کاربر اختصاص یافت.", back_button("admin:users"))
        try:
            deliver_purchase_message(target_id, purchase_id)
        except Exception:
            pass
        return

    # ── Admin: Agents management ──────────────────────────────────────────────
    if data == "admin:agents":
        if not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        agents    = get_agencies()
        req_flag  = setting_get("agency_request_enabled", "1")
        req_icon  = "🟢" if req_flag == "1" else "🔴"
        req_label = "روشن" if req_flag == "1" else "خاموش"
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            f"{req_icon} درخواست نمایندگی — {req_label}",
            callback_data="adm:agt:toggle"))
        kb.add(types.InlineKeyboardButton("➕ اضافه کردن نماینده", callback_data="adm:agt:add"))
        # Inline list: each agent on one row with remove button
        for ag in agents:
            name = esc(ag["full_name"]) if ag["full_name"] else str(ag["user_id"])
            kb.row(
                types.InlineKeyboardButton(
                    f"🤝 {name}",
                    callback_data=f"adm:agt:u:{ag['user_id']}"),
                types.InlineKeyboardButton(
                    "🗑 حذف",
                    callback_data=f"adm:agt:rm:{ag['user_id']}")
            )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
        send_or_edit(call,
            f"🤝 <b>مدیریت نمایندگان</b>\n\n"
            f"👥 تعداد نمایندگان فعلی: <b>{len(agents)}</b>\n"
            f"📨 وضعیت درخواست: <b>{req_label}</b>",
            kb)
        return

    if data == "adm:agt:add":
        if not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_agent_add_search")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔍 <b>جستجوی کاربر برای افزودن به نمایندگی</b>\n\n"
            "آیدی عددی یا یوزرنیم کاربر را ارسال کنید:",
            back_button("admin:agents"))
        return

    if data.startswith("adm:agt:u:"):
        target_uid = int(data.split(":")[3])
        bot.answer_callback_query(call.id)
        _show_admin_user_detail(call, target_uid)
        return

    if data.startswith("adm:agt:rm:"):
        if not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[3])
        with get_conn() as conn:
            conn.execute("UPDATE users SET is_agent=0 WHERE user_id=?", (target_uid,))
        bot.answer_callback_query(call.id, "✅ کاربر از نمایندگی حذف شد.")
        # re-render agents menu
        agents    = get_agencies()
        req_flag  = setting_get("agency_request_enabled", "1")
        req_icon  = "🟢" if req_flag == "1" else "🔴"
        req_label = "روشن" if req_flag == "1" else "خاموش"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            f"{req_icon} درخواست نمایندگی — {req_label}",
            callback_data="adm:agt:toggle"))
        kb.add(types.InlineKeyboardButton("➕ اضافه کردن نماینده", callback_data="adm:agt:add"))
        for ag in agents:
            name = esc(ag["full_name"]) if ag["full_name"] else str(ag["user_id"])
            kb.row(
                types.InlineKeyboardButton(
                    f"🤝 {name}",
                    callback_data=f"adm:agt:u:{ag['user_id']}"),
                types.InlineKeyboardButton(
                    "🗑 حذف",
                    callback_data=f"adm:agt:rm:{ag['user_id']}")
            )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
        send_or_edit(call,
            f"🤝 <b>مدیریت نمایندگان</b>\n\n"
            f"👥 تعداد نمایندگان فعلی: <b>{len(agents)}</b>\n"
            f"📨 وضعیت درخواست: <b>{req_label}</b>",
            kb)
        return

    if data == "adm:agt:toggle":
        if not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur      = setting_get("agency_request_enabled", "1")
        new      = "0" if cur == "1" else "1"
        setting_set("agency_request_enabled", new)
        log_admin_action(uid, f"درخواست نمایندگی {'فعال' if new == '1' else 'غیرفعال'} شد")
        req_icon  = "🟢" if new == "1" else "🔴"
        req_label = "روشن" if new == "1" else "خاموش"
        bot.answer_callback_query(call.id, f"درخواست نمایندگی: {req_label}")
        agents = get_agencies()
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            f"{req_icon} درخواست نمایندگی — {req_label}",
            callback_data="adm:agt:toggle"))
        kb.add(types.InlineKeyboardButton("➕ اضافه کردن نماینده", callback_data="adm:agt:add"))
        for ag in agents:
            name = esc(ag["full_name"]) if ag["full_name"] else str(ag["user_id"])
            kb.row(
                types.InlineKeyboardButton(
                    f"🤝 {name}",
                    callback_data=f"adm:agt:u:{ag['user_id']}"),
                types.InlineKeyboardButton(
                    "🗑 حذف",
                    callback_data=f"adm:agt:rm:{ag['user_id']}")
            )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
        send_or_edit(call,
            f"🤝 <b>مدیریت نمایندگان</b>\n\n"
            f"👥 تعداد نمایندگان فعلی: <b>{len(agents)}</b>\n"
            f"📨 وضعیت درخواست: <b>{req_label}</b>",
            kb)
        return

    # ── Agency price config (3-mode) ──────────────────────────────────────────
    if data.startswith("adm:agcfg:") and data.count(":") == 2:
        # adm:agcfg:{target_id}  — show mode selector
        parts     = data.split(":")
        target_id = int(parts[2])
        if not admin_has_perm(uid, "agency") and not admin_has_perm(uid, "full_users"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cfg  = get_agency_price_config(target_id)
        mode = cfg["price_mode"]
        tick = {m: "✅ " for m in ["global", "type", "package"]}
        for k in tick:
            tick[k] = "✅ " if mode == k else ""
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            f"{tick['global']}🌍 تخفیف روی کل محصولات",
            callback_data=f"adm:agcfg:global:{target_id}"))
        kb.add(types.InlineKeyboardButton(
            f"{tick['type']}🧩 تخفیف روی هر دسته",
            callback_data=f"adm:agcfg:type:{target_id}"))
        kb.add(types.InlineKeyboardButton(
            f"{tick['package']}📦 قیمت جداگانه هر پکیج",
            callback_data=f"adm:agcfg:pkg:{target_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
        bot.answer_callback_query(call.id)
        target_user = get_user(target_id)
        uname = esc(target_user["full_name"]) if target_user else str(target_id)
        mode_labels = {"global": "🌍 تخفیف کل محصولات", "type": "🧩 تخفیف هر دسته", "package": "📦 قیمت هر پکیج"}
        send_or_edit(call,
            f"💰 <b>قیمت نمایندگی کاربر</b>\n"
            f"👤 {uname}\n\n"
            f"حالت فعلی: <b>{mode_labels.get(mode, mode)}</b>\n\n"
            "حالت مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("adm:agcfg:global:") and data.count(":") == 3:
        # adm:agcfg:global:{target_id}  — choose pct or toman
        target_id = int(data.split(":")[3])
        cfg = get_agency_price_config(target_id)
        g_type = cfg["global_type"]
        g_val  = cfg["global_val"]
        cur_label = f"{'درصد' if g_type == 'pct' else 'تومان'} — مقدار فعلی: {g_val}"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("📊 درصد", callback_data=f"adm:agcfg:glb:pct:{target_id}"),
            types.InlineKeyboardButton("💵 تومان", callback_data=f"adm:agcfg:glb:tmn:{target_id}"),
        )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:agcfg:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"🌍 <b>تخفیف کل محصولات</b>\n\n"
            f"تنظیم فعلی: <b>{cur_label}</b>\n\n"
            "می‌خواهی درصد کم بشه یا مبلغ ثابت (تومان)؟", kb)
        return

    if data.startswith("adm:agcfg:glb:"):
        # adm:agcfg:glb:pct:{target_id}  or  adm:agcfg:glb:tmn:{target_id}
        parts     = data.split(":")
        dtype     = parts[3]   # pct or tmn
        target_id = int(parts[4])
        set_agency_price_config(target_id, "global", "pct" if dtype == "pct" else "toman", 0)
        state_set(uid, "admin_agcfg_global_val", target_user_id=target_id, dtype=dtype)
        bot.answer_callback_query(call.id)
        label = "درصد تخفیف (مثال: 20)" if dtype == "pct" else "مبلغ تخفیف به تومان (مثال: 50000)"
        send_or_edit(call,
            f"🌍 <b>تخفیف کل محصولات</b>\n\n"
            f"{'📊' if dtype == 'pct' else '💵'} {label} را وارد کنید:",
            back_button(f"adm:agcfg:global:{target_id}"))
        return

    if data.startswith("adm:agcfg:type:") and data.count(":") == 3:
        # adm:agcfg:type:{target_id}  — show types list
        target_id = int(data.split(":")[3])
        types_list = get_all_types()
        if not types_list:
            bot.answer_callback_query(call.id, "هیچ نوعی تعریف نشده.", show_alert=True)
            return
        set_agency_price_config(target_id, "type",
            get_agency_price_config(target_id)["global_type"],
            get_agency_price_config(target_id)["global_val"])
        kb = types.InlineKeyboardMarkup()
        for t in types_list:
            td = get_agency_type_discount(target_id, t["id"])
            if td:
                dot = "✅"
                val_lbl = f"{td['discount_value']}{'%' if td['discount_type']=='pct' else 'ت'}"
            else:
                dot = "⬜️"
                val_lbl = "تنظیم نشده"
            kb.add(types.InlineKeyboardButton(
                f"{dot} {t['name']} | {val_lbl}",
                callback_data=f"adm:agcfg:td:{target_id}:{t['id']}"
            ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:agcfg:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🧩 <b>تخفیف هر دسته</b>\n\nدسته مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("adm:agcfg:td:") and data.count(":") == 4:
        # adm:agcfg:td:{target_id}:{type_id}  — choose pct or toman for this type
        parts     = data.split(":")
        target_id = int(parts[3])
        type_id   = int(parts[4])
        type_row  = get_type(type_id) if hasattr(__import__('bot.db', fromlist=['get_type']), 'get_type') else None
        td = get_agency_type_discount(target_id, type_id)
        cur_label = f"{'درصد' if td['discount_type']=='pct' else 'تومان'} — {td['discount_value']}" if td else "تنظیم نشده"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("📊 درصد", callback_data=f"adm:agcfg:tdt:{target_id}:{type_id}:pct"),
            types.InlineKeyboardButton("💵 تومان", callback_data=f"adm:agcfg:tdt:{target_id}:{type_id}:tmn"),
        )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:agcfg:type:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"🧩 <b>دسته #{type_id}</b>\n\n"
            f"تنظیم فعلی: <b>{cur_label}</b>\n\n"
            "می‌خواهی درصد کم بشه یا مبلغ ثابت؟", kb)
        return

    if data.startswith("adm:agcfg:tdt:"):
        # adm:agcfg:tdt:{target_id}:{type_id}:pct  or  :tmn
        parts     = data.split(":")
        target_id = int(parts[3])
        type_id   = int(parts[4])
        dtype     = parts[5]
        state_set(uid, "admin_agcfg_type_val",
                  target_user_id=target_id, type_id=type_id, dtype=dtype)
        bot.answer_callback_query(call.id)
        label = "درصد (مثال: 15)" if dtype == "pct" else "مبلغ تومان (مثال: 30000)"
        send_or_edit(call,
            f"🧩 دسته #{type_id}\n\n"
            f"{'📊' if dtype == 'pct' else '💵'} {label} را وارد کنید:",
            back_button(f"adm:agcfg:td:{target_id}:{type_id}"))
        return

    if data.startswith("adm:agcfg:pkg:"):
        # adm:agcfg:pkg:{target_id}  — show packages (existing flow)
        target_id = int(data.split(":")[3])
        set_agency_price_config(target_id, "package",
            get_agency_price_config(target_id)["global_type"],
            get_agency_price_config(target_id)["global_val"])
        packs = get_packages()
        if not packs:
            bot.answer_callback_query(call.id, "پکیجی موجود نیست.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        for p in packs:
            ap    = get_agency_price(target_id, p["id"])
            price = fmt_price(ap) if ap is not None else fmt_price(p["price"])
            label = f"{p['name']} | {price} ت"
            kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:usr:agpe:{target_id}:{p['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:agcfg:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 <b>قیمت هر پکیج</b>\n\nبرای ویرایش روی پکیج بزنید:", kb)
        return

    # ── Admin: Broadcast ──────────────────────────────────────────────────────
    if data == "admin:broadcast":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📣 همه کاربران",             callback_data="adm:bc:all"))
        kb.add(types.InlineKeyboardButton("🛍 فقط مشتریان (همه)",       callback_data="adm:bc:cust"))
        kb.add(types.InlineKeyboardButton("👤 فقط مشتریان عادی",        callback_data="adm:bc:normal"))
        kb.add(types.InlineKeyboardButton("🤝 فقط نمایندگان",           callback_data="adm:bc:agents"))
        kb.add(types.InlineKeyboardButton("👑 فقط ادمین‌ها",            callback_data="adm:bc:admins"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",                  callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📣 <b>فوروارد همگانی</b>\n\nگیرنده‌ها را انتخاب کنید:", kb)
        return

    if data == "adm:bc:all":
        state_set(uid, "admin_broadcast_all")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📣 پیام خود را فوروارد یا ارسال کنید.\nبرای <b>همه کاربران</b> ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    if data == "adm:bc:cust":
        state_set(uid, "admin_broadcast_customers")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🛍 پیام خود را فوروارد یا ارسال کنید.\nفقط برای <b>مشتریان</b> ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    if data == "adm:bc:normal":
        state_set(uid, "admin_broadcast_normal")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "👤 پیام خود را فوروارد یا ارسال کنید.\nفقط برای <b>مشتریان عادی</b> (بدون نمایندگان و ادمین‌ها) ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    if data == "adm:bc:agents":
        state_set(uid, "admin_broadcast_agents")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🤝 پیام خود را فوروارد یا ارسال کنید.\nفقط برای <b>نمایندگان</b> ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    if data == "adm:bc:admins":
        state_set(uid, "admin_broadcast_admins")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "👑 پیام خود را فوروارد یا ارسال کنید.\nفقط برای <b>ادمین‌ها</b> ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    # ── Admin: Group management ───────────────────────────────────────────────
    if data == "admin:group":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        gid      = get_group_id()
        active_c = _count_active_topics()
        total_c  = len(TOPICS)
        gid_text = f"<code>{gid}</code>" if gid else "تنظیم نشده"
        text = (
            "🏢 <b>مدیریت گروه ادمین</b>\n\n"
            "📌 <b>راهنما:</b>\n"
            "۱. یک سوپرگروه تلگرام بسازید و Topics را فعال کنید.\n"
            "۲. ربات را به گروه اضافه و ادمین کنید.\n"
            "۳. آیدی عددی گروه را با @getidsbot دریافت کنید.\n"
            "۴. دکمه «ثبت آیدی گروه» را بزنید و آیدی را ارسال کنید.\n\n"
            "ℹ️ آیدی گروه با <code>-100</code> شروع می‌شود. مثال: <code>-1001234567890</code>\n\n"
            f"📊 <b>وضعیت:</b> گروه {gid_text} | تاپیک‌ها: {active_c}/{total_c}"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔢 ثبت آیدی گروه",      callback_data="adm:grp:setid"))
        kb.add(types.InlineKeyboardButton("🛠 ساخت تاپیک‌های جدید",  callback_data="adm:grp:create"))
        kb.add(types.InlineKeyboardButton("♻️ بازسازی همه تاپیک‌ها", callback_data="adm:grp:reset"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:grp:setid":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_set_group_id")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔢 <b>آیدی عددی گروه</b> را ارسال کنید:\n\n"
            "مثال: <code>-1001234567890</code>\n\n"
            "برای دریافت آیدی گروه، ربات <b>@getidsbot</b> را به گروه اضافه کنید و <code>/id</code> بفرستید.",
            back_button("admin:group"))
        return

    if data == "adm:grp:create":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "در حال ساخت تاپیک‌ها...", show_alert=False)
        result = ensure_group_topics()
        log_admin_action(uid, "ساخت تاپیک‌های گروه")
        send_or_edit(call, f"🛠 <b>ساخت تاپیک</b>\n\n{result}", back_button("admin:group"))
        return

    if data == "adm:grp:reset":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "در حال بازسازی...", show_alert=False)
        result = reset_and_recreate_topics()
        log_admin_action(uid, "بازسازی تاپیک‌های گروه")
        send_or_edit(call, f"♻️ <b>بازسازی تاپیک‌ها</b>\n\n{result}", back_button("admin:group"))
        return

    # ── Admin: Settings ───────────────────────────────────────────────────────
    if data == "admin:settings":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🎧 پشتیبانی",           callback_data="adm:set:support"),
            types.InlineKeyboardButton("💳 درگاه‌های پرداخت",   callback_data="adm:set:gateways"),
        )
        kb.add(types.InlineKeyboardButton("📢 کانال قفل",        callback_data="adm:set:channel"))
        kb.add(types.InlineKeyboardButton("✏️ ویرایش متن استارت", callback_data="adm:set:start_text"))
        kb.add(types.InlineKeyboardButton("🎁 تست رایگان",      callback_data="adm:set:freetest"))
        kb.add(types.InlineKeyboardButton("📜 قوانین خرید",     callback_data="adm:set:rules"))
        kb.add(types.InlineKeyboardButton("🏪 مدیریت فروش",    callback_data="adm:set:shop"))
        kb.add(types.InlineKeyboardButton("🤖 مدیریت عملیات ربات", callback_data="adm:ops"))
        kb.add(types.InlineKeyboardButton("🏢 مدیریت گروه",    callback_data="admin:group"))
        kb.add(types.InlineKeyboardButton("📌 پیام‌های پین شده", callback_data="adm:pin"))
        kb.add(types.InlineKeyboardButton("� مدیریت اعلان‌ها",  callback_data="adm:notif"))
        kb.add(types.InlineKeyboardButton("�💾 بکاپ",            callback_data="admin:backup"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",        callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "⚙️ <b>تنظیمات</b>", kb)
        return

    if data == "adm:set:agency_toggle":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("agency_request_enabled", "1")
        new = "0" if cur == "1" else "1"
        setting_set("agency_request_enabled", new)
        log_admin_action(uid, f"درخواست نمایندگی از تنظیمات {'فعال' if new == '1' else 'غیرفعال'} شد")
        label = "فعال" if new == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"درخواست نمایندگی: {label}")
        # re-render settings
        _fake_call_data = type('obj', (object,), {
            'id': call.id, 'message': call.message,
            'data': 'admin:settings', 'from_user': call.from_user
        })()
        _fake_call_data.id = call.id
        try:
            agency_flag  = new
            agency_icon  = "✅" if agency_flag == "1" else "❌"
            pct          = setting_get("agency_default_discount_pct", "20")
            kb           = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("🎧 پشتیبانی",           callback_data="adm:set:support"),
                types.InlineKeyboardButton("💳 درگاه‌های پرداخت",   callback_data="adm:set:gateways"),
            )
            kb.add(types.InlineKeyboardButton("📢 کانال قفل",        callback_data="adm:set:channel"))
            kb.add(types.InlineKeyboardButton("✏️ ویرایش متن استارت", callback_data="adm:set:start_text"))
            kb.add(types.InlineKeyboardButton("🎁 تست رایگان",      callback_data="adm:set:freetest"))
            kb.add(types.InlineKeyboardButton("📜 قوانین خرید",     callback_data="adm:set:rules"))
            kb.add(types.InlineKeyboardButton("🏷 تنظیمات فروش",    callback_data="adm:set:shop"))
            kb.add(types.InlineKeyboardButton("🏢 مدیریت گروه",    callback_data="admin:group"))
            kb.add(types.InlineKeyboardButton("📌 پیام‌های پین شده", callback_data="adm:pin"))
            kb.add(types.InlineKeyboardButton(f"{agency_icon} درخواست نمایندگی", callback_data="adm:set:agency_toggle"))
            kb.add(types.InlineKeyboardButton("📊 تخفیف پیش‌فرض نمایندگی", callback_data="adm:set:agency_defpct"))
            kb.add(types.InlineKeyboardButton("� مدیریت اعلان‌ها",  callback_data="adm:notif"))
            kb.add(types.InlineKeyboardButton("�💾 بکاپ",            callback_data="admin:backup"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت",        callback_data="admin:panel"))
            send_or_edit(call, "⚙️ <b>تنظیمات</b>", kb)
        except Exception:
            pass
        return

    if data == "adm:set:agency_defpct":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur_pct = setting_get("agency_default_discount_pct", "20")
        state_set(uid, "admin_set_default_discount_pct")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"📊 <b>تخفیف پیش‌فرض نمایندگی</b>\n\n"
            f"تنظیم فعلی: <b>{cur_pct}%</b>\n\n"
            "درصد جدید را وارد کنید (عدد بین 0 تا 100):",
            back_button("admin:settings"))
        return

    # ── Notification Management ───────────────────────────────────────────────
    # Notification types: (key, label)
    _NOTIF_TYPES = [
        ("new_users",        "👋 کاربر جدید"),
        ("payment_approval", "💳 تأیید پرداخت"),
        ("renewal_request",  "♻️ درخواست تمدید"),
        ("purchase_log",     "📦 لاگ خرید"),
        ("renewal_log",      "🔄 لاگ تمدید"),
        ("wallet_log",       "💰 لاگ کیف‌پول"),
        ("test_report",      "🧪 گزارش تست"),
        ("broadcast_report", "📢 اطلاع‌رسانی و پین"),
        ("referral_log",     "🔗 زیرمجموعه‌گیری"),
        ("agency_request",   "🤝 درخواست نمایندگی"),
        ("agency_log",       "🏢 لاگ نمایندگان"),
        ("admin_ops_log",    "📝 لاگ عملیاتی"),
        ("error_log",        "❌ گزارش خطا"),
        ("backup",           "💾 بکاپ"),
    ]

    if data == "adm:notif":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("👑 اعلان های ربات اونر",   callback_data="adm:notif:own"))
        kb.add(types.InlineKeyboardButton("🤖 اعلان های ربات ادمین",   callback_data="adm:notif:bot"))
        kb.add(types.InlineKeyboardButton("📢 گروه",  callback_data="adm:notif:grp"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        send_or_edit(call,
            "🔔 <b>مدیریت اعلان‌ها</b>\n\n"
            "👑 <b>اعلان های ربات اونر</b>: اعلان برای اونر در ربات\n"
            "🤖 <b>اعلان های ربات ادمین</b>: اعلان برای ادمین‌های فرعی (بر اساس دسترسی)\n"
            "📢 <b>گروه</b>: اعلان در تاپیک‌های گروه",
            kb)
        return

    if data == "adm:notif:own":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        for key, label in _NOTIF_TYPES:
            on = setting_get(f"notif_own_{key}", "1") == "1"
            icon = "✅" if on else "❌"
            kb.add(types.InlineKeyboardButton(
                f"{icon} {label}",
                callback_data=f"adm:notif:otg:{key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:notif"))
        send_or_edit(call,
            "👑 <b>اعلان های ربات اونر</b>\n\n"
            "اعلان‌هایی که مستقیماً برای <b>ADMIN_IDS</b> (اید ثابت تو config.py) ارسال می‌شن:"
            "\n✅ = فعال  |  ❌ = غیرفعال",
            kb)
        return

    if data.startswith("adm:notif:otg:"):
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        key = data[len("adm:notif:otg:"):]
        cur = setting_get(f"notif_own_{key}", "1")
        new = "0" if cur == "1" else "1"
        setting_set(f"notif_own_{key}", new)
        log_admin_action(uid, f"اعلان شخصی {key} {'فعال' if new == '1' else 'غیرفعال'} شد")
        label_map = dict(_NOTIF_TYPES)
        lbl = label_map.get(key, key)
        status_lbl = "فعال" if new == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"{status_lbl} شد: {lbl}")
        kb = types.InlineKeyboardMarkup()
        for k, l in _NOTIF_TYPES:
            on = setting_get(f"notif_own_{k}", "1") == "1"
            icon = "✅" if on else "❌"
            kb.add(types.InlineKeyboardButton(
                f"{icon} {l}",
                callback_data=f"adm:notif:otg:{k}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:notif"))
        send_or_edit(call,
            "👑 <b>اعلان های ربات اونر</b>\n\n"
            "اعلان‌هایی که مستقیماً برای <b>ADMIN_IDS</b> ارسال می‌شن:"
            "\n✅ = فعال  |  ❌ = غیرفعال",
            kb)
        return

    if data == "adm:notif:grp":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        for key, label in _NOTIF_TYPES:
            on = setting_get(f"notif_grp_{key}", "1") == "1"
            icon = "✅" if on else "❌"
            kb.add(types.InlineKeyboardButton(
                f"{icon} {label}",
                callback_data=f"adm:notif:gtg:{key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:notif"))
        send_or_edit(call,
            "📢 <b>گروه</b>\n\n"
            "انتخاب کنید کدام اعلان‌ها در تاپیک‌های گروه ارسال شوند:\n"
            "✅ = فعال  |  ❌ = غیرفعال",
            kb)
        return

    if data == "adm:notif:bot":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        for key, label in _NOTIF_TYPES:
            on = setting_get(f"notif_bot_{key}", "1") == "1"
            icon = "✅" if on else "❌"
            kb.add(types.InlineKeyboardButton(
                f"{icon} {label}",
                callback_data=f"adm:notif:btg:{key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:notif"))
        send_or_edit(call,
            "🤖 <b>اعلان های ربات ادمین</b>\n\n"
            "انتخاب کنید کدام اعلان‌ها به صورت مستقیم برای ادمین‌ها ارسال شوند:\n"
            "✅ = فعال  |  ❌ = غیرفعال",
            kb)
        return

    if data.startswith("adm:notif:gtg:"):
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        key = data[len("adm:notif:gtg:"):]
        cur = setting_get(f"notif_grp_{key}", "1")
        new = "0" if cur == "1" else "1"
        setting_set(f"notif_grp_{key}", new)
        log_admin_action(uid, f"اعلان گروه {key} {'فعال' if new == '1' else 'غیرفعال'} شد")
        label_map = dict(_NOTIF_TYPES)
        lbl = label_map.get(key, key)
        status_lbl = "فعال" if new == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"{status_lbl} شد: {lbl}")
        # re-render group list
        kb = types.InlineKeyboardMarkup()
        for k, l in _NOTIF_TYPES:
            on = setting_get(f"notif_grp_{k}", "1") == "1"
            icon = "✅" if on else "❌"
            kb.add(types.InlineKeyboardButton(
                f"{icon} {l}",
                callback_data=f"adm:notif:gtg:{k}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:notif"))
        send_or_edit(call,
            "📢 <b>گروه</b>\n\n"
            "انتخاب کنید کدام اعلان‌ها در تاپیک‌های گروه ارسال شوند:\n"
            "✅ = فعال  |  ❌ = غیرفعال",
            kb)
        return

    if data.startswith("adm:notif:btg:"):
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        key = data[len("adm:notif:btg:"):]
        cur = setting_get(f"notif_bot_{key}", "1")
        new = "0" if cur == "1" else "1"
        setting_set(f"notif_bot_{key}", new)
        log_admin_action(uid, f"اعلان ربات {key} {'فعال' if new == '1' else 'غیرفعال'} شد")
        label_map = dict(_NOTIF_TYPES)
        lbl = label_map.get(key, key)
        status_lbl = "فعال" if new == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"{status_lbl} شد: {lbl}")
        # re-render bot list
        kb = types.InlineKeyboardMarkup()
        for k, l in _NOTIF_TYPES:
            on = setting_get(f"notif_bot_{k}", "1") == "1"
            icon = "✅" if on else "❌"
            kb.add(types.InlineKeyboardButton(
                f"{icon} {l}",
                callback_data=f"adm:notif:btg:{k}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:notif"))
        send_or_edit(call,
            "🤖 <b>اعلان های ربات ادمین</b>\n\n"
            "انتخاب کنید کدام اعلان‌ها به صورت مستقیم برای ادمین‌ها ارسال شوند:\n"
            "✅ = فعال  |  ❌ = غیرفعال",
            kb)
        return
    # ── End Notification Management ───────────────────────────────────────────

    if data == "adm:set:support":
        support_raw = setting_get("support_username", "")
        support_link = setting_get("support_link", "")
        support_link_desc = setting_get("support_link_desc", "")
        kb = types.InlineKeyboardMarkup()
        tg_status = "✅" if support_raw else "❌"
        link_status = "✅" if support_link else "❌"
        kb.add(types.InlineKeyboardButton(f"{tg_status} پشتیبانی تلگرام", callback_data="adm:set:support_tg"))
        kb.add(types.InlineKeyboardButton(f"{link_status} پشتیبانی آنلاین (لینک)", callback_data="adm:set:support_link"))
        kb.add(types.InlineKeyboardButton("✏️ توضیحات پشتیبانی", callback_data="adm:set:support_desc"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        text = (
            "🎧 <b>تنظیمات پشتیبانی</b>\n\n"
            f"📱 تلگرام: <code>{esc(support_raw or 'ثبت نشده')}</code>\n"
            f"🌐 لینک: <code>{esc(support_link or 'ثبت نشده')}</code>\n"
            f"📝 توضیحات: {esc(support_link_desc or 'پیش‌فرض')}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:set:support_tg":
        state_set(uid, "admin_set_support")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🎧 آیدی یا لینک پشتیبانی تلگرام را ارسال کنید.\nمثال: <code>@username</code>",
                     back_button("adm:set:support"))
        return

    if data == "adm:set:support_link":
        state_set(uid, "admin_set_support_link")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🌐 لینک پشتیبانی آنلاین را ارسال کنید.\nمثال: <code>https://example.com/chat</code>\n\nبرای حذف، <code>-</code> بفرستید.",
                     back_button("adm:set:support"))
        return

    if data == "adm:set:support_desc":
        state_set(uid, "admin_set_support_desc")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📝 توضیحات نمایشی بالای دکمه‌های پشتیبانی را بنویسید.\n\nبرای بازگشت به پیش‌فرض، <code>-</code> بفرستید.",
                     back_button("adm:set:support"))
        return

    # ── Shop management settings ─────────────────────────────────────────────
    if data == "adm:set:shop":
        shop_open     = setting_get("shop_open", "1")
        preorder_mode = setting_get("preorder_mode", "0")
        open_icon  = "🟢" if shop_open     == "1" else "🔴"
        stock_icon = "🟢" if preorder_mode == "1" else "🔴"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            f"{open_icon} وضعیت فروش: {'باز' if shop_open == '1' else 'بسته'}",
            callback_data="adm:shop:toggle_open"))
        kb.add(types.InlineKeyboardButton(
            f"{stock_icon} فروش بر اساس موجودی: {'فعال' if preorder_mode == '1' else 'غیرفعال'}",
            callback_data="adm:shop:toggle_stock"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        text = (
            "🏪 <b>مدیریت فروش</b>\n\n"
            f"🔹 <b>وضعیت فروش:</b> {'🟢 باز' if shop_open == '1' else '🔴 بسته'}\n"
            f"🔹 <b>فروش بر اساس موجودی:</b> {'🟢 فعال – فقط پکیج‌های دارای موجودی نمایش داده می‌شوند.' if preorder_mode == '1' else '🔴 غیرفعال – همه پکیج‌ها نمایش داده می‌شوند. در صورت نبود موجودی، سفارش به پشتیبانی ارسال می‌شود.'}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:shop:toggle_open":
        current = setting_get("shop_open", "1")
        setting_set("shop_open", "0" if current == "1" else "1")
        log_admin_action(uid, f"فروشگاه {'بسته' if current == '1' else 'باز'} شد")
        bot.answer_callback_query(call.id, "وضعیت فروش تغییر کرد.")
        # Re-show shop settings
        data = "adm:set:shop"
        # fall through by calling the handler again via fake callback
        from types import SimpleNamespace as _SN
        fake = _SN(id=call.id, from_user=call.from_user, message=call.message, data=data)
        _dispatch_callback(fake, uid, data)
        return

    if data == "adm:shop:toggle_stock":
        current = setting_get("preorder_mode", "0")
        setting_set("preorder_mode", "0" if current == "1" else "1")
        log_admin_action(uid, f"حالت پیش‌فروش {'غیرفعال' if current == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تنظیم فروش بر اساس موجودی تغییر کرد.")
        from types import SimpleNamespace as _SN
        fake = _SN(id=call.id, from_user=call.from_user, message=call.message, data="adm:set:shop")
        _dispatch_callback(fake, uid, "adm:set:shop")
        return

    # ── Bot Operations Management ─────────────────────────────────────────────
    def _build_ops_kb():
        bot_status      = setting_get("bot_status", "on")
        renewal_enabled = setting_get("manual_renewal_enabled", "1")
        referral_enabled = setting_get("referral_enabled", "1")
        status_map = {"on": "🟢 روشن", "off": "🔴 خاموش", "update": "🔄 بروزرسانی"}
        renewal_map = {"1": "✅ فعال", "0": "❌ غیرفعال"}
        referral_map = {"1": "✅ فعال", "0": "❌ غیرفعال"}
        status_label  = status_map.get(bot_status, "🟢 روشن")
        renewal_label = renewal_map.get(renewal_enabled, "✅ فعال")
        referral_label = referral_map.get(referral_enabled, "✅ فعال")
        ops_kb = types.InlineKeyboardMarkup(row_width=2)
        ops_kb.row(
            types.InlineKeyboardButton(status_label,  callback_data="adm:ops:status"),
            types.InlineKeyboardButton("🤖 وضعیت ربات", callback_data="adm:ops:noop"),
        )
        ops_kb.row(
            types.InlineKeyboardButton(renewal_label, callback_data="adm:ops:renewal"),
            types.InlineKeyboardButton("♻️ تمدید کانفیگ‌های ثبت دستی", callback_data="adm:ops:noop"),
        )
        ops_kb.row(
            types.InlineKeyboardButton(referral_label, callback_data="adm:ops:referral_toggle"),
            types.InlineKeyboardButton("🎁 زیرمجموعه‌گیری", callback_data="adm:ops:noop"),
        )
        ops_kb.add(types.InlineKeyboardButton("⚙️ تنظیمات زیرمجموعه‌گیری", callback_data="adm:ref:settings"))
        ops_kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        return ops_kb

    def _ops_menu_text():
        bot_status      = setting_get("bot_status", "on")
        renewal_enabled = setting_get("manual_renewal_enabled", "1")
        referral_enabled = setting_get("referral_enabled", "1")
        status_fa  = {"on": "🟢 روشن", "off": "🔴 خاموش", "update": "🔄 بروزرسانی"}.get(bot_status, "🟢 روشن")
        renewal_fa = "✅ فعال" if renewal_enabled == "1" else "❌ غیرفعال"
        referral_fa = "✅ فعال" if referral_enabled == "1" else "❌ غیرفعال"
        return (
            "🤖 <b>مدیریت عملیات ربات</b>\n\n"
            f"🔹 <b>وضعیت ربات:</b> {status_fa}\n"
            f"🔹 <b>تمدید کانفیگ‌های ثبت دستی:</b> {renewal_fa}\n"
            f"🔹 <b>زیرمجموعه‌گیری:</b> {referral_fa}\n\n"
            "برای تغییر هر مورد، دکمه وضعیت فعلی آن را لمس کنید."
        )

    if data == "adm:ops":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        send_or_edit(call, _ops_menu_text(), _build_ops_kb())
        return

    if data == "adm:ops:noop":
        bot.answer_callback_query(call.id)
        return

    if data == "adm:ops:status":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("bot_status", "on")
        cycle = {"on": "off", "off": "update", "update": "on"}
        new_status = cycle.get(cur, "on")
        setting_set("bot_status", new_status)
        labels = {"on": "روشن", "off": "خاموش", "update": "بروزرسانی"}
        log_admin_action(uid, f"وضعیت ربات به {labels[new_status]} تغییر کرد")
        bot.answer_callback_query(call.id, f"وضعیت ربات: {labels[new_status]}")
        send_or_edit(call, _ops_menu_text(), _build_ops_kb())
        return

    if data == "adm:ops:renewal":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("manual_renewal_enabled", "1")
        new_val = "0" if cur == "1" else "1"
        setting_set("manual_renewal_enabled", new_val)
        log_admin_action(uid, f"تمدید دستی {'فعال' if new_val == '1' else 'غیرفعال'} شد")
        label = "فعال" if new_val == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"تمدید دستی: {label}")
        send_or_edit(call, _ops_menu_text(), _build_ops_kb())
        return

    if data == "adm:ops:referral_toggle":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("referral_enabled", "1")
        new_val = "0" if cur == "1" else "1"
        setting_set("referral_enabled", new_val)
        log_admin_action(uid, f"زیرمجموعه‌گیری {'فعال' if new_val == '1' else 'غیرفعال'} شد")
        label = "فعال" if new_val == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"زیرمجموعه‌گیری: {label}")
        send_or_edit(call, _ops_menu_text(), _build_ops_kb())
        return

    # ── Referral Settings ─────────────────────────────────────────────────────
    def _ref_settings_kb():
        sr_enabled = setting_get("referral_start_reward_enabled", "0")
        pr_enabled = setting_get("referral_purchase_reward_enabled", "0")
        sr_label = "✅ فعال" if sr_enabled == "1" else "❌ غیرفعال"
        pr_label = "✅ فعال" if pr_enabled == "1" else "❌ غیرفعال"
        sr_type = setting_get("referral_start_reward_type", "wallet")
        pr_type = setting_get("referral_purchase_reward_type", "wallet")
        sr_count = setting_get("referral_start_reward_count", "1")
        pr_count = setting_get("referral_purchase_reward_count", "1")
        sr_type_label = "💰 کیف پول" if sr_type == "wallet" else "📦 کانفیگ"
        pr_type_label = "💰 کیف پول" if pr_type == "wallet" else "📦 کانفیگ"

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📸 تنظیم بنر اشتراک‌گذاری", callback_data="adm:ref:banner"))
        # Start reward section
        kb.add(types.InlineKeyboardButton("── 🎁 هدیه استارت ──", callback_data="adm:ops:noop"))
        kb.row(
            types.InlineKeyboardButton(sr_label, callback_data="adm:ref:sr:toggle"),
            types.InlineKeyboardButton("وضعیت هدیه استارت", callback_data="adm:ops:noop"),
        )
        kb.add(types.InlineKeyboardButton(f"📊 تعداد: {sr_count} زیرمجموعه", callback_data="adm:ref:sr:count"))
        kb.add(types.InlineKeyboardButton(f"🎯 نوع هدیه: {sr_type_label}", callback_data="adm:ref:sr:type"))
        if sr_type == "wallet":
            sr_amount = setting_get("referral_start_reward_amount", "0")
            kb.add(types.InlineKeyboardButton(f"💵 مبلغ: {fmt_price(int(sr_amount))} تومان", callback_data="adm:ref:sr:amount"))
        else:
            sr_pkg = setting_get("referral_start_reward_package", "")
            pkg_name = "انتخاب نشده"
            if sr_pkg:
                _p = get_package(int(sr_pkg)) if sr_pkg.isdigit() else None
                if _p:
                    pkg_name = _p["name"]
            kb.add(types.InlineKeyboardButton(f"📦 پکیج: {pkg_name}", callback_data="adm:ref:sr:pkg"))

        # Purchase reward section
        kb.add(types.InlineKeyboardButton("── 💸 هدیه خرید ──", callback_data="adm:ops:noop"))
        kb.row(
            types.InlineKeyboardButton(pr_label, callback_data="adm:ref:pr:toggle"),
            types.InlineKeyboardButton("وضعیت هدیه خرید", callback_data="adm:ops:noop"),
        )
        kb.add(types.InlineKeyboardButton(f"📊 تعداد: {pr_count} خرید", callback_data="adm:ref:pr:count"))
        kb.add(types.InlineKeyboardButton(f"🎯 نوع هدیه: {pr_type_label}", callback_data="adm:ref:pr:type"))
        if pr_type == "wallet":
            pr_amount = setting_get("referral_purchase_reward_amount", "0")
            kb.add(types.InlineKeyboardButton(f"💵 مبلغ: {fmt_price(int(pr_amount))} تومان", callback_data="adm:ref:pr:amount"))
        else:
            pr_pkg = setting_get("referral_purchase_reward_package", "")
            pkg_name = "انتخاب نشده"
            if pr_pkg:
                _p = get_package(int(pr_pkg)) if pr_pkg.isdigit() else None
                if _p:
                    pkg_name = _p["name"]
            kb.add(types.InlineKeyboardButton(f"📦 پکیج: {pkg_name}", callback_data="adm:ref:pr:pkg"))

        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:ops"))
        return kb

    def _ref_settings_text():
        sr_enabled = "✅ فعال" if setting_get("referral_start_reward_enabled", "0") == "1" else "❌ غیرفعال"
        pr_enabled = "✅ فعال" if setting_get("referral_purchase_reward_enabled", "0") == "1" else "❌ غیرفعال"
        return (
            "⚙️ <b>تنظیمات زیرمجموعه‌گیری</b>\n\n"
            f"🎁 هدیه استارت: {sr_enabled}\n"
            f"💸 هدیه خرید زیرمجموعه: {pr_enabled}\n\n"
            "هر بخش را با دکمه‌های زیر تنظیم کنید."
        )

    if data == "adm:ref:settings":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    if data == "adm:ref:banner":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_ref_banner")
        bot.answer_callback_query(call.id)
        cur_text = setting_get("referral_banner_text", "")
        cur_photo = setting_get("referral_banner_photo", "")
        status = ""
        if cur_text:
            status += f"\n\n📝 متن فعلی:\n{esc(cur_text[:200])}"
        if cur_photo:
            status += "\n🖼 عکس: ✅ ست شده"
        kb = types.InlineKeyboardMarkup()
        if cur_text or cur_photo:
            kb.add(types.InlineKeyboardButton("🗑 حذف بنر سفارشی", callback_data="adm:ref:banner:del"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:ref:settings"))
        send_or_edit(call,
            "📸 <b>تنظیم بنر اشتراک‌گذاری</b>\n\n"
            "متن یا عکس+کپشن مورد نظر برای اشتراک‌گذاری لینک دعوت ارسال کنید.\n"
            "این متن/عکس هنگام اشتراک‌گذاری لینک دعوت به کاربران نمایش داده می‌شود.\n\n"
            "💡 لینک دعوت کاربر به صورت خودکار به انتهای متن اضافه می‌شود."
            f"{status}", kb)
        return

    if data == "adm:ref:banner:del":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        setting_set("referral_banner_text", "")
        setting_set("referral_banner_photo", "")
        log_admin_action(uid, "بنر اشتراک‌گذاری حذف شد")
        bot.answer_callback_query(call.id, "بنر سفارشی حذف شد.")
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    # Start reward toggles
    if data == "adm:ref:sr:toggle":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("referral_start_reward_enabled", "0")
        setting_set("referral_start_reward_enabled", "0" if cur == "1" else "1")
        log_admin_action(uid, f"هدیه استارت زیرمجموعه {'غیرفعال' if cur == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id)
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    if data == "adm:ref:sr:count":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_ref_sr_count")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔢 <b>تعداد زیرمجموعه برای هدیه استارت</b>\n\n"
            "ادمین عزیز، وارد کنید بعد از چند زیرمجموعه جدید، هدیه به معرف داده شود.\n\n"
            f"مقدار فعلی: <b>{setting_get('referral_start_reward_count', '1')}</b>",
            back_button("adm:ref:settings"))
        return

    if data == "adm:ref:sr:type":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("referral_start_reward_type", "wallet")
        new_val = "config" if cur == "wallet" else "wallet"
        setting_set("referral_start_reward_type", new_val)
        log_admin_action(uid, f"نوع هدیه استارت به {'کیف پول' if new_val == 'wallet' else 'کانفیگ'} تغییر کرد")
        bot.answer_callback_query(call.id, f"نوع هدیه: {'کیف پول' if new_val == 'wallet' else 'کانفیگ'}")
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    if data == "adm:ref:sr:amount":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_ref_sr_amount")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "💵 <b>مبلغ شارژ کیف پول (هدیه استارت)</b>\n\n"
            "مبلغ به تومان وارد کنید:\n\n"
            f"مقدار فعلی: <b>{fmt_price(int(setting_get('referral_start_reward_amount', '0')))}</b> تومان",
            back_button("adm:ref:settings"))
        return

    if data == "adm:ref:sr:pkg":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        # Show package list for selection
        all_types = get_all_types()
        kb = types.InlineKeyboardMarkup()
        for t in all_types:
            pkgs = get_packages(t["id"])
            for p in pkgs:
                kb.add(types.InlineKeyboardButton(
                    f"{t['name']} - {p['name']}",
                    callback_data=f"adm:ref:sr:pkgsel:{p['id']}"
                ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:ref:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 <b>انتخاب پکیج هدیه استارت</b>\n\nپکیجی که می‌خواهید به عنوان هدیه داده شود انتخاب کنید:", kb)
        return

    if data.startswith("adm:ref:sr:pkgsel:"):
        pkg_id = data.split(":")[4]
        setting_set("referral_start_reward_package", pkg_id)
        log_admin_action(uid, f"پکیج هدیه استارت به #{pkg_id} تنظیم شد")
        bot.answer_callback_query(call.id, "پکیج هدیه استارت تنظیم شد.")
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    # Purchase reward toggles
    if data == "adm:ref:pr:toggle":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("referral_purchase_reward_enabled", "0")
        setting_set("referral_purchase_reward_enabled", "0" if cur == "1" else "1")
        log_admin_action(uid, f"هدیه خرید زیرمجموعه {'غیرفعال' if cur == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id)
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    if data == "adm:ref:pr:count":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_ref_pr_count")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔢 <b>تعداد خرید زیرمجموعه برای هدیه</b>\n\n"
            "وارد کنید بعد از چند خرید اول زیرمجموعه‌ها، هدیه به معرف داده شود.\n"
            "⚠️ فقط اولین خرید هر زیرمجموعه در نظر گرفته می‌شود.\n\n"
            f"مقدار فعلی: <b>{setting_get('referral_purchase_reward_count', '1')}</b>",
            back_button("adm:ref:settings"))
        return

    if data == "adm:ref:pr:type":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        cur = setting_get("referral_purchase_reward_type", "wallet")
        new_val = "config" if cur == "wallet" else "wallet"
        setting_set("referral_purchase_reward_type", new_val)
        log_admin_action(uid, f"نوع هدیه خرید به {'کیف پول' if new_val == 'wallet' else 'کانفیگ'} تغییر کرد")
        bot.answer_callback_query(call.id, f"نوع هدیه: {'کیف پول' if new_val == 'wallet' else 'کانفیگ'}")
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    if data == "adm:ref:pr:amount":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_ref_pr_amount")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "💵 <b>مبلغ شارژ کیف پول (هدیه خرید)</b>\n\n"
            "مبلغ به تومان وارد کنید:\n\n"
            f"مقدار فعلی: <b>{fmt_price(int(setting_get('referral_purchase_reward_amount', '0')))}</b> تومان",
            back_button("adm:ref:settings"))
        return

    if data == "adm:ref:pr:pkg":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        all_types = get_all_types()
        kb = types.InlineKeyboardMarkup()
        for t in all_types:
            pkgs = get_packages(t["id"])
            for p in pkgs:
                kb.add(types.InlineKeyboardButton(
                    f"{t['name']} - {p['name']}",
                    callback_data=f"adm:ref:pr:pkgsel:{p['id']}"
                ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:ref:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 <b>انتخاب پکیج هدیه خرید</b>\n\nپکیجی که می‌خواهید به عنوان هدیه داده شود انتخاب کنید:", kb)
        return

    if data.startswith("adm:ref:pr:pkgsel:"):
        pkg_id = data.split(":")[4]
        setting_set("referral_purchase_reward_package", pkg_id)
        log_admin_action(uid, f"پکیج هدیه خرید به #{pkg_id} تنظیم شد")
        bot.answer_callback_query(call.id, "پکیج هدیه خرید تنظیم شد.")
        send_or_edit(call, _ref_settings_text(), _ref_settings_kb())
        return

    # ── Gateway settings ─────────────────────────────────────────────────────
    if data == "adm:set:gateways":
        kb = types.InlineKeyboardMarkup()
        for gw_key, gw_default in [
            ("card",             "💳 کارت به کارت"),
            ("crypto",           "💎 ارز دیجیتال"),
            ("tetrapay",         "💳 درگاه کارت به کارت (TetraPay)"),
            ("swapwallet_crypto","💳 درگاه کارت به کارت و ارز دیجیتال (SwapWallet)"),
            ("tronpays_rial",    "💳 درگاه کارت به کارت (TronsPay)"),
        ]:
            enabled = setting_get(f"gw_{gw_key}_enabled", "0")
            status_icon = "🟢" if enabled == "1" else "🔴"
            gw_label = setting_get(f"gw_{gw_key}_display_name", "").strip() or gw_default
            kb.add(types.InlineKeyboardButton(f"{status_icon} {gw_label}", callback_data=f"adm:set:gw:{gw_key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💳 <b>درگاه‌های پرداخت</b>\n\nدرگاه مورد نظر را انتخاب کنید:", kb)
        return

    if data == "adm:set:gw:card":
        enabled = setting_get("gw_card_enabled", "0")
        vis = setting_get("gw_card_visibility", "public")
        card = setting_get("payment_card", "")
        bank = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        range_enabled = setting_get("gw_card_range_enabled", "0")
        display_name = setting_get("gw_card_display_name", "")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        range_label = "🟢 فعال" if range_enabled == "1" else "🔴 غیرفعال"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:card:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:card:vis"),
        )
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label}", callback_data="adm:gw:card:range"))
        kb.add(types.InlineKeyboardButton("🏷 نام نمایشی درگاه", callback_data="adm:gw:card:set_name"))
        kb.add(types.InlineKeyboardButton("💳 شماره کارت", callback_data="adm:set:card"))
        kb.add(types.InlineKeyboardButton("🏦 نام بانک", callback_data="adm:set:bank"))
        kb.add(types.InlineKeyboardButton("👤 نام صاحب کارت", callback_data="adm:set:owner"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        name_display = display_name or "<i>پیش‌فرض: کارت به کارت</i>"
        text = (
            "💳 <b>درگاه کارت به کارت</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n"
            f"نام نمایشی: {name_display}\n\n"
            f"کارت: <code>{esc(card or 'ثبت نشده')}</code>\n"
            f"بانک: {esc(bank or 'ثبت نشده')}\n"
            f"صاحب: {esc(owner or 'ثبت نشده')}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:card:set_name":
        state_set(uid, "admin_set_gw_display_name", gw="card")
        bot.answer_callback_query(call.id)
        current = setting_get("gw_card_display_name", "")
        send_or_edit(call,
            f"🏷 <b>نام نمایشی درگاه کارت به کارت</b>\n\n"
            f"مقدار فعلی: <code>{esc(current or 'پیش‌فرض')}</code>\n\n"
            "نام دلخواه را ارسال کنید.\n"
            "برای بازگشت به پیش‌فرض، <code>-</code> ارسال کنید.",
            back_button("adm:set:gw:card"))
        return

    if data == "adm:gw:card:toggle":
        enabled = setting_get("gw_card_enabled", "0")
        setting_set("gw_card_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"درگاه کارت {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:card")
        return

    if data == "adm:gw:card:vis":
        vis = setting_get("gw_card_visibility", "public")
        setting_set("gw_card_visibility", "secure" if vis == "public" else "public")
        log_admin_action(uid, f"نمایش درگاه کارت به {'secure' if vis == 'public' else 'public'} تغییر کرد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:card")
        return

    if data == "adm:set:gw:crypto":
        enabled = setting_get("gw_crypto_enabled", "0")
        vis = setting_get("gw_crypto_visibility", "public")
        range_enabled = setting_get("gw_crypto_range_enabled", "0")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        range_label = "🟢 فعال" if range_enabled == "1" else "🔴 غیرفعال"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:crypto:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:crypto:vis"),
        )
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label}", callback_data="adm:gw:crypto:range"))
        kb.add(types.InlineKeyboardButton("🏷 نام نمایشی درگاه", callback_data="adm:gw:crypto:set_name"))
        for coin_key, coin_label in CRYPTO_COINS:
            addr = setting_get(f"crypto_{coin_key}", "")
            status_icon = "✅" if addr else "❌"
            kb.add(types.InlineKeyboardButton(f"{status_icon} {coin_label}", callback_data=f"adm:set:cw:{coin_key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        display_name_crypto = setting_get("gw_crypto_display_name", "")
        name_display_crypto = display_name_crypto or "<i>پیش‌فرض: ارز دیجیتال</i>"
        text = (
            "💎 <b>درگاه ارز دیجیتال</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n"
            f"نام نمایشی: {name_display_crypto}\n\n"
            "برای ویرایش آدرس ولت روی هر ارز بزنید:"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:crypto:set_name":
        state_set(uid, "admin_set_gw_display_name", gw="crypto")
        bot.answer_callback_query(call.id)
        current = setting_get("gw_crypto_display_name", "")
        send_or_edit(call,
            f"🏷 <b>نام نمایشی درگاه ارز دیجیتال</b>\n\n"
            f"مقدار فعلی: <code>{esc(current or 'پیش‌فرض')}</code>\n\n"
            "نام دلخواه را ارسال کنید.\n"
            "برای بازگشت به پیش‌فرض، <code>-</code> ارسال کنید.",
            back_button("adm:set:gw:crypto"))
        return

    if data == "adm:gw:crypto:toggle":
        enabled = setting_get("gw_crypto_enabled", "0")
        setting_set("gw_crypto_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"درگاه کریپتو {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:crypto")
        return

    if data == "adm:gw:crypto:vis":
        vis = setting_get("gw_crypto_visibility", "public")
        setting_set("gw_crypto_visibility", "secure" if vis == "public" else "public")
        log_admin_action(uid, f"نمایش درگاه کریپتو به {'secure' if vis == 'public' else 'public'} تغییر کرد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:crypto")
        return

    if data == "adm:set:gw:tetrapay":
        enabled = setting_get("gw_tetrapay_enabled", "0")
        vis = setting_get("gw_tetrapay_visibility", "public")
        api_key = setting_get("tetrapay_api_key", "")
        mode_bot = setting_get("tetrapay_mode_bot", "1")
        mode_web = setting_get("tetrapay_mode_web", "1")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        bot_label = "🟢 فعال" if mode_bot == "1" else "🔴 غیرفعال"
        web_label = "🟢 فعال" if mode_web == "1" else "🔴 غیرفعال"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:tetrapay:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:tetrapay:vis"),
        )
        kb.row(
            types.InlineKeyboardButton(f"تلگرام: {bot_label}", callback_data="adm:gw:tetrapay:mode_bot"),
            types.InlineKeyboardButton(f"مرورگر: {web_label}", callback_data="adm:gw:tetrapay:mode_web"),
        )
        range_enabled_tp = setting_get("gw_tetrapay_range_enabled", "0")
        range_label_tp = "🟢 فعال" if range_enabled_tp == "1" else "🔴 غیرفعال"
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label_tp}", callback_data="adm:gw:tetrapay:range"))
        kb.add(types.InlineKeyboardButton("🏷 نام نمایشی درگاه", callback_data="adm:gw:tetrapay:set_name"))
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API", callback_data="adm:set:tetrapay_key"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سایت TetraPay", url="https://tetra98.com"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        if api_key:
            key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>"
        else:
            key_display = "❌ <b>ثبت نشده</b> — ابتدا از سایت TetraPay کلید API خود را دریافت کنید"
        display_name_tp = setting_get("gw_tetrapay_display_name", "")
        name_display_tp = display_name_tp or "<i>پیش‌فرض: درگاه کارت به کارت (TetraPay)</i>"
        text = (
            "💳 <b>درگاه کارت به کارت (TetraPay)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n"
            f"نام نمایشی: {name_display_tp}\n\n"
            f"💳 پرداخت از تلگرام: {bot_label}\n"
            f"🌐 پرداخت از مرورگر: {web_label}\n\n"
            f"کلید API: {key_display}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:tetrapay:set_name":
        state_set(uid, "admin_set_gw_display_name", gw="tetrapay")
        bot.answer_callback_query(call.id)
        current = setting_get("gw_tetrapay_display_name", "")
        send_or_edit(call,
            f"🏷 <b>نام نمایشی درگاه TetraPay</b>\n\n"
            f"مقدار فعلی: <code>{esc(current or 'پیش‌فرض')}</code>\n\n"
            "نام دلخواه را ارسال کنید.\n"
            "برای بازگشت به پیش‌فرض، <code>-</code> ارسال کنید.",
            back_button("adm:set:gw:tetrapay"))
        return

    if data == "adm:gw:tetrapay:toggle":
        enabled = setting_get("gw_tetrapay_enabled", "0")
        setting_set("gw_tetrapay_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"درگاه تتراپی {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:vis":
        vis = setting_get("gw_tetrapay_visibility", "public")
        setting_set("gw_tetrapay_visibility", "secure" if vis == "public" else "public")
        log_admin_action(uid, f"نمایش درگاه تتراپی به {'secure' if vis == 'public' else 'public'} تغییر کرد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:mode_bot":
        cur = setting_get("tetrapay_mode_bot", "1")
        setting_set("tetrapay_mode_bot", "0" if cur == "1" else "1")
        log_admin_action(uid, f"حالت bot تتراپی {'غیرفعال' if cur == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:mode_web":
        cur = setting_get("tetrapay_mode_web", "1")
        setting_set("tetrapay_mode_web", "0" if cur == "1" else "1")
        log_admin_action(uid, f"حالت web تتراپی {'غیرفعال' if cur == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:set:tetrapay_key":
        state_set(uid, "admin_set_tetrapay_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API تتراپی را ارسال کنید:", back_button("adm:set:gw:tetrapay"))
        return

    if data == "adm:set:gw:swapwallet_crypto":
        from ..gateways.swapwallet_crypto import NETWORK_LABELS as SW_CRYPTO_LABELS
        enabled  = setting_get("gw_swapwallet_crypto_enabled", "0")
        vis      = setting_get("gw_swapwallet_crypto_visibility", "public")
        api_key  = setting_get("swapwallet_crypto_api_key", "")
        username = setting_get("swapwallet_crypto_username", "")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label     = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:swapwallet_crypto:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}",    callback_data="adm:gw:swapwallet_crypto:vis"),
        )
        range_en = setting_get("gw_swapwallet_crypto_range_enabled", "0")
        range_label = "🟢 فعال" if range_en == "1" else "🔴 غیرفعال"
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label}", callback_data="adm:gw:swapwallet_crypto:range"))
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API",        callback_data="adm:set:swapwallet_crypto_key"))
        kb.add(types.InlineKeyboardButton("👤 نام کاربری فروشگاه",     callback_data="adm:set:swapwallet_crypto_username"))
        kb.add(types.InlineKeyboardButton("🏷 نام نمایشی درگاه", callback_data="adm:gw:swapwallet_crypto:set_name"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سواپ ولت", url="https://swapwallet.app"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>" if api_key else "❌ <b>ثبت نشده — الزامی</b>"
        user_status = "✅ ثبت شده" if username else "❌ ثبت نشده"
        display_name_sw = setting_get("gw_swapwallet_crypto_display_name", "")
        name_display_sw = display_name_sw or "<i>پیش‌فرض: درگاه کارت به کارت و ارز دیجیتال (SwapWallet)</i>"
        text = (
            "💳 <b>درگاه کارت به کارت و ارز دیجیتال (SwapWallet)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n"
            f"نام نمایشی: {name_display_sw}\n\n"
            f"👤 نام کاربری Application: <code>{esc(username or 'ثبت نشده')}</code> {user_status}\n"
            f"🔑 کلید API: {key_display}\n\n"
            "📖 <b>شبکه‌های پشتیبانی:</b> TRON · TON · BSC\n\n"
            "📖 <b>مراحل راه‌اندازی:</b>\n"
            "1️⃣ در مینی‌اپ سواپ‌ولت استارت بزنید:\n"
            "   👉 @SwapWalletBot\n"
            "2️⃣ در پنل بیزنس با تلگرام لاگین کنید:\n"
            "   👉 business.swapwallet.app\n"
            "3️⃣ یک فروشگاه جدید بسازید\n"
            "4️⃣ <b>نام فروشگاه</b> رو به عنوان نام کاربری اینجا وارد کنید\n"
            "5️⃣ از تب <b>پروفایل ← کلید API</b> کلید بگیرید و وارد کنید"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:swapwallet_crypto:set_name":
        state_set(uid, "admin_set_gw_display_name", gw="swapwallet_crypto")
        bot.answer_callback_query(call.id)
        current = setting_get("gw_swapwallet_crypto_display_name", "")
        send_or_edit(call,
            f"🏷 <b>نام نمایشی درگاه SwapWallet</b>\n\n"
            f"مقدار فعلی: <code>{esc(current or 'پیش‌فرض')}</code>\n\n"
            "نام دلخواه را ارسال کنید.\n"
            "برای بازگشت به پیش‌فرض، <code>-</code> ارسال کنید.",
            back_button("adm:set:gw:swapwallet_crypto"))
        return

    if data == "adm:gw:swapwallet_crypto:toggle":
        enabled = setting_get("gw_swapwallet_crypto_enabled", "0")
        setting_set("gw_swapwallet_crypto_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"درگاه سواپ‌ولت کریپتو {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet_crypto")
        return

    if data == "adm:gw:swapwallet_crypto:vis":
        vis = setting_get("gw_swapwallet_crypto_visibility", "public")
        setting_set("gw_swapwallet_crypto_visibility", "secure" if vis == "public" else "public")
        log_admin_action(uid, f"نمایش درگاه سواپ‌ولت کریپتو به {'secure' if vis == 'public' else 'public'} تغییر کرد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet_crypto")
        return

    if data == "adm:set:swapwallet_crypto_key":
        state_set(uid, "admin_set_swapwallet_crypto_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔑 <b>کلید API (SwapWallet کریپتو) را ارسال کنید</b>\n\n"
            "فرمت: <code>apikey-xxx...</code>\n\n"
            "📍 برای دریافت:\n"
            "اپ سواپ‌ولت ← پروفایل ← <b>کلید API</b>",
            back_button("adm:set:gw:swapwallet_crypto"))
        return

    if data == "adm:set:swapwallet_crypto_username":
        state_set(uid, "admin_set_swapwallet_crypto_username")
        bot.answer_callback_query(call.id)
        current = setting_get("swapwallet_crypto_username", "")
        send_or_edit(call,
            f"👤 <b>نام کاربری فروشگاه (SwapWallet کریپتو) را ارسال کنید</b>\n\n"
            f"این همان <b>نام فروشگاه</b> شما در پنل بیزنس است.\n"
            f"مقدار فعلی: <code>{esc(current or 'ثبت نشده')}</code>",
            back_button("adm:set:gw:swapwallet_crypto"))
        return

    if data == "adm:set:gw:tronpays_rial":
        enabled = setting_get("gw_tronpays_rial_enabled", "0")
        vis     = setting_get("gw_tronpays_rial_visibility", "public")
        api_key = setting_get("tronpays_rial_api_key", "")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label     = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        range_en      = setting_get("gw_tronpays_rial_range_enabled", "0")
        range_label   = "🟢 فعال" if range_en == "1" else "🔴 غیرفعال"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:tronpays_rial:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}",     callback_data="adm:gw:tronpays_rial:vis"),
        )
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label}", callback_data="adm:gw:tronpays_rial:range"))
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API", callback_data="adm:set:tronpays_rial_key"))
        kb.add(types.InlineKeyboardButton("🔗 تنظیم Callback URL", callback_data="adm:set:tronpays_rial_cb_url"))
        kb.add(types.InlineKeyboardButton("🏷 نام نمایشی درگاه", callback_data="adm:gw:tronpays_rial:set_name"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🤖 دریافت API Key از @TronPaysBot", url="https://t.me/TronPaysBot"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        key_display = (f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>"
                       if api_key else "❌ <b>ثبت نشده</b> — ابتدا از ربات @TronPaysBot کلید API دریافت کنید")
        cb_url = setting_get("tronpays_rial_callback_url", "").strip() or "https://example.com/"
        display_name_tp_rial = setting_get("gw_tronpays_rial_display_name", "")
        name_display_tp_rial = display_name_tp_rial or "<i>پیش‌فرض: درگاه کارت به کارت (TronsPay)</i>"
        text = (
            "💳 <b>درگاه کارت به کارت (TronsPay)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n"
            f"نام نمایشی: {name_display_tp_rial}\n\n"
            f"🔑 کلید API: {key_display}\n"
            f"🔗 Callback URL: <code>{esc(cb_url)}</code>\n\n"
            "📋 <b>راهنمای دریافت API Key:</b>\n"
            "۱. ربات @TronPaysBot را استارت کنید\n"
            "۲. ثبت‌نام و احراز هویت را تکمیل کنید\n"
            "۳. کلید API را از پروفایل دریافت کنید"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:tronpays_rial:set_name":
        state_set(uid, "admin_set_gw_display_name", gw="tronpays_rial")
        bot.answer_callback_query(call.id)
        current = setting_get("gw_tronpays_rial_display_name", "")
        send_or_edit(call,
            f"🏷 <b>نام نمایشی درگاه TronsPay</b>\n\n"
            f"مقدار فعلی: <code>{esc(current or 'پیش‌فرض')}</code>\n\n"
            "نام دلخواه را ارسال کنید.\n"
            "برای بازگشت به پیش‌فرض، <code>-</code> ارسال کنید.",
            back_button("adm:set:gw:tronpays_rial"))
        return

    if data == "adm:gw:tronpays_rial:toggle":
        enabled = setting_get("gw_tronpays_rial_enabled", "0")
        setting_set("gw_tronpays_rial_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"درگاه ترون‌پیز ریالی {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tronpays_rial")
        return

    if data == "adm:gw:tronpays_rial:vis":
        vis = setting_get("gw_tronpays_rial_visibility", "public")
        setting_set("gw_tronpays_rial_visibility", "secure" if vis == "public" else "public")
        log_admin_action(uid, f"نمایش درگاه ترون‌پیز ریالی به {'secure' if vis == 'public' else 'public'} تغییر کرد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tronpays_rial")
        return

    if data == "adm:set:tronpays_rial_key":
        state_set(uid, "admin_set_tronpays_rial_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API TronPays را ارسال کنید:", back_button("adm:set:gw:tronpays_rial"))
        return

    if data == "adm:set:tronpays_rial_cb_url":
        state_set(uid, "admin_set_tronpays_rial_cb_url")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔗 <b>Callback URL درگاه TronPays</b>\n\n"
            "یک URL معتبر ارسال کنید (مثلاً آدرس سایت یا وبهوک شما).\n"
            "اگر ندارید، <code>https://example.com/</code> را بفرستید.",
            back_button("adm:set:gw:tronpays_rial"))
        return

    _GW_RANGE_LABELS = {"card": "💳 کارت به کارت", "crypto": "💎 ارز دیجیتال", "tetrapay": "🏦 TetraPay", "swapwallet": "💎 SwapWallet", "swapwallet_crypto": "💎 SwapWallet کریپتو", "tronpays_rial": "💳 TronPays"}

    if data.startswith("adm:gw:") and data.endswith(":range"):
        gw_name = data.split(":")[2]
        gw_label = _GW_RANGE_LABELS.get(gw_name, gw_name)
        range_enabled = setting_get(f"gw_{gw_name}_range_enabled", "0")
        range_min = setting_get(f"gw_{gw_name}_range_min", "")
        range_max = setting_get(f"gw_{gw_name}_range_max", "")
        enabled_label = "🟢 فعال" if range_enabled == "1" else "🔴 غیرفعال"
        min_label = fmt_price(int(range_min)) + " تومان" if range_min else "بدون حداقل"
        max_label = fmt_price(int(range_max)) + " تومان" if range_max else "بدون حداکثر"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(f"وضعیت بازه: {enabled_label}", callback_data=f"adm:gw:{gw_name}:range:toggle"))
        kb.add(types.InlineKeyboardButton("✏️ تنظیم بازه", callback_data=f"adm:gw:{gw_name}:range:set"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:set:gw:{gw_name}"))
        text = (
            f"📊 <b>بازه پرداختی — {gw_label}</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"حداقل مبلغ: {min_label}\n"
            f"حداکثر مبلغ: {max_label}\n\n"
            "⚠️ اگر بازه فعال باشد، این درگاه فقط برای مبالغ داخل بازه نمایش داده می‌شود."
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:gw:") and data.endswith(":range:toggle"):
        gw_name = data.split(":")[2]
        cur = setting_get(f"gw_{gw_name}_range_enabled", "0")
        setting_set(f"gw_{gw_name}_range_enabled", "0" if cur == "1" else "1")
        log_admin_action(uid, f"بازه مبلغ درگاه {gw_name} {'غیرفعال' if cur == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, f"adm:gw:{gw_name}:range")
        return

    if data.startswith("adm:gw:") and data.endswith(":range:set"):
        gw_name = data.split(":")[2]
        state_set(uid, "admin_gw_range_min", gw=gw_name)
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "📊 <b>حداقل مبلغ</b> (تومان) را وارد کنید.\n\n"
            "برای <b>بدون حداقل</b>، عدد <code>0</code> یا <code>-</code> ارسال کنید:",
            back_button(f"adm:gw:{gw_name}:range"))
        return

    if data == "adm:set:payment":
        _fake_call(call, "adm:set:gw:card")
        bot.answer_callback_query(call.id)
        return

    if data == "adm:set:cardvis":
        _fake_call(call, "adm:gw:card:vis")
        bot.answer_callback_query(call.id)
        return

    if data == "adm:set:card":
        state_set(uid, "admin_set_card")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💳 شماره کارت را ارسال کنید:", back_button("adm:set:gw:card"))
        return

    if data == "adm:set:bank":
        state_set(uid, "admin_set_bank")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🏦 نام بانک را ارسال کنید:", back_button("adm:set:gw:card"))
        return

    if data == "adm:set:owner":
        state_set(uid, "admin_set_owner")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "👤 نام و نام خانوادگی صاحب کارت را ارسال کنید:", back_button("adm:set:gw:card"))
        return

    if data == "adm:set:crypto":
        _fake_call(call, "adm:set:gw:crypto")
        bot.answer_callback_query(call.id)
        return

    if data.startswith("adm:set:cw:"):
        coin_key   = data.split(":")[3]
        coin_label = next((l for k, l in CRYPTO_COINS if k == coin_key), coin_key)
        state_set(uid, "admin_set_crypto_wallet", coin_key=coin_key)
        bot.answer_callback_query(call.id)
        current    = setting_get(f"crypto_{coin_key}", "")
        send_or_edit(
            call,
            f"💎 آدرس ولت <b>{coin_label}</b> را وارد کنید.\n"
            f"آدرس فعلی: <code>{esc(current or 'ثبت نشده')}</code>\n\n"
            "برای حذف، عدد <code>-</code> بفرستید.",
            back_button("adm:set:gw:crypto")
        )
        return

    if data == "adm:set:channel":
        current = setting_get("channel_id", "")
        state_set(uid, "admin_set_channel")
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            f"📢 <b>کانال قفل</b>\n\n"
            f"کانال فعلی: {esc(current or 'ثبت نشده')}\n\n"
            "@username کانال را وارد کنید\n"
            "برای غیرفعال کردن، <code>-</code> بفرستید\n\n"
            "⚠️ ربات باید ادمین کانال باشد",
            back_button("admin:settings")
        )
        return

    if data == "adm:set:start_text":
        current = setting_get("start_text", "")
        state_set(uid, "admin_set_start_text")
        bot.answer_callback_query(call.id)
        preview = esc(current[:200]) + "..." if len(current) > 200 else esc(current or "پیش‌فرض")
        send_or_edit(
            call,
            f"✏️ <b>ویرایش متن استارت</b>\n\n"
            f"متن فعلی:\n{preview}\n\n"
            "متن جدید را ارسال کنید. می‌توانید از تگ‌های HTML استفاده کنید.\n"
            "برای بازگشت به متن پیش‌فرض، <code>-</code> بفرستید.",
            back_button("admin:settings")
        )
        return

    # ── Admin: Free Test Settings ─────────────────────────────────────────────
    if data == "adm:set:freetest":
        enabled = setting_get("free_test_enabled", "1")
        agent_limit = setting_get("agent_test_limit", "0")
        agent_period = setting_get("agent_test_period", "day")
        period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
        kb = types.InlineKeyboardMarkup()
        toggle_label = "🔴 غیرفعال کردن" if enabled == "1" else "🟢 فعال کردن"
        kb.add(types.InlineKeyboardButton(toggle_label, callback_data="adm:ft:toggle"))
        kb.add(types.InlineKeyboardButton("🔄 ریست تست رایگان همه کاربران", callback_data="adm:ft:reset"))
        kb.add(types.InlineKeyboardButton(f"🤝 تعداد تست همکاران: {agent_limit} در {period_labels.get(agent_period, agent_period)}", callback_data="adm:ft:agent"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            f"🎁 <b>تنظیمات تست رایگان</b>\n\n"
            f"وضعیت: {'🟢 فعال' if enabled == '1' else '🔴 غیرفعال'}\n"
            f"تست همکاران: <b>{agent_limit}</b> عدد در {period_labels.get(agent_period, agent_period)}",
            kb
        )
        return

    if data == "adm:ft:toggle":
        enabled = setting_get("free_test_enabled", "1")
        setting_set("free_test_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"تست رایگان {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:freetest")
        return

    if data == "adm:ft:reset":
        reset_all_free_tests()
        bot.answer_callback_query(call.id, "✅ تست رایگان همه کاربران ریست شد.", show_alert=True)
        _fake_call(call, "adm:set:freetest")
        return

    if data == "adm:ft:agent":
        state_set(uid, "admin_set_agent_test_limit")
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            "🤝 <b>تعداد تست همکاران</b>\n\n"
            "تعداد تست رایگان همکاران را وارد کنید.\n"
            "فرمت: <code>تعداد بازه</code>\n\n"
            "مثال:\n"
            "<code>5 day</code> → ۵ تست در روز\n"
            "<code>10 week</code> → ۱۰ تست در هفته\n"
            "<code>20 month</code> → ۲۰ تست در ماه\n\n"
            "برای غیرفعال کردن محدودیت، <code>0</code> بفرستید.",
            back_button("adm:set:freetest")
        )
        return

    # ── Admin: Purchase Rules ─────────────────────────────────────────────────
    if data == "adm:set:rules":
        enabled = setting_get("purchase_rules_enabled", "0")
        kb = types.InlineKeyboardMarkup()
        toggle_label = "🔴 غیرفعال کردن" if enabled == "1" else "🟢 فعال کردن"
        kb.add(types.InlineKeyboardButton(toggle_label, callback_data="adm:rules:toggle"))
        kb.add(types.InlineKeyboardButton("✏️ ویرایش متن قوانین", callback_data="adm:rules:edit"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"📜 <b>قوانین خرید</b>\n\n"
            f"وضعیت: {'🟢 فعال' if enabled == '1' else '🔴 غیرفعال'}\n\n"
            "وقتی فعال باشد، کاربر قبل از اولین خرید باید قوانین را بپذیرد.", kb)
        return

    if data == "adm:rules:toggle":
        enabled = setting_get("purchase_rules_enabled", "0")
        setting_set("purchase_rules_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"قوانین خرید {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:rules")
        return

    if data == "adm:rules:edit":
        state_set(uid, "admin_edit_rules_text")
        bot.answer_callback_query(call.id)
        current_text = setting_get("purchase_rules_text", "")
        preview = f"\n\n📝 متن فعلی:\n{esc(current_text[:200])}..." if len(current_text) > 200 else (f"\n\n📝 متن فعلی:\n{esc(current_text)}" if current_text else "")
        send_or_edit(call,
            f"✏️ <b>ویرایش متن قوانین خرید</b>{preview}\n\n"
            "متن جدید قوانین خرید را ارسال کنید:",
            back_button("adm:set:rules"))
        return

    if data == "buy:accept_rules":
        # User accepted rules, mark and proceed to buy
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"rules_accepted_{uid}", "1")
            )
        bot.answer_callback_query(call.id)
        # Proceed to buy flow
        _fake_call(call, "buy:start_real")
        return

    # ── Admin: Pinned Messages ─────────────────────────────────────────────────
    if data == "adm:pin":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        pins = get_all_pinned_messages()
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("➕ افزودن پیام پین", callback_data="adm:pin:add"))
        for p in pins:
            preview = (p["text"] or "")[:30].replace("\n", " ")
            kb.row(
                types.InlineKeyboardButton(f"📌 {preview}", callback_data="noop"),
                types.InlineKeyboardButton("✏️", callback_data=f"adm:pin:edit:{p['id']}"),
                types.InlineKeyboardButton("🗑", callback_data=f"adm:pin:del:{p['id']}"),
            )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        count_text = f"{len(pins)} پیام" if pins else "هیچ پیامی ثبت نشده"
        send_or_edit(call, f"📌 <b>پیام‌های پین شده</b>\n\n{count_text}", kb)
        return

    if data == "adm:pin:add":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_pin_add")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📌 <b>افزودن پیام پین</b>\n\nمتن پیام را ارسال کنید:", back_button("adm:pin"))
        return

    if data.startswith("adm:pin:del:"):
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        pin_id = int(data.split(":")[3])
        # Get pin text before deleting for log
        _pin_row = get_pinned_message(pin_id)
        _pin_text_preview = ""
        if _pin_row:
            _pin_text_preview = (_pin_row["text"] or "")[:200].strip()
        # Unpin and delete sent messages from all user chats
        sends = get_pinned_sends(pin_id)
        removed_count = 0
        for s in sends:
            try:
                bot.unpin_chat_message(s["user_id"], s["message_id"])
            except Exception:
                pass
            try:
                bot.delete_message(s["user_id"], s["message_id"])
                removed_count += 1
            except Exception:
                pass
        delete_pinned_sends(pin_id)
        delete_pinned_message(pin_id)
        log_admin_action(uid, f"پیام پین #{pin_id} حذف شد")
        bot.answer_callback_query(call.id, "🗑 پیام حذف و آنپین شد.")
        send_to_topic("broadcast_report",
            f"🗑 <b>حذف پیام پین</b>\n\n"
            f"👤 حذف‌کننده: <code>{uid}</code>\n"
            f"🗑 حذف شده از: <b>{removed_count}</b> کاربر\n\n"
            f"📝 <b>متن پیام:</b>\n{esc(_pin_text_preview) if _pin_text_preview else '(خالی)'}")
        _fake_call(call, "adm:pin")
        return

    if data.startswith("adm:pin:edit:"):
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        pin_id = int(data.split(":")[3])
        pin = get_pinned_message(pin_id)
        if not pin:
            bot.answer_callback_query(call.id, "پیام یافت نشد.", show_alert=True)
            return
        state_set(uid, "admin_pin_edit", pin_id=pin_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"✏️ <b>ویرایش پیام پین</b>\n\nمتن فعلی:\n<code>{esc(pin['text'])}</code>\n\nمتن جدید را ارسال کنید:",
            back_button("adm:pin"))
        return

    # ── Admin: Backup ─────────────────────────────────────────────────────────
    if data == "admin:backup":
        enabled  = setting_get("backup_enabled", "0")
        interval = setting_get("backup_interval", "24")
        target   = setting_get("backup_target_id", "")
        kb       = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💾 بکاپ دستی", callback_data="adm:bkp:manual"))
        kb.add(types.InlineKeyboardButton("📥 بازیابی بکاپ", callback_data="adm:bkp:restore"))
        toggle_label = "🔴 غیرفعال کردن بکاپ خودکار" if enabled == "1" else "🟢 فعال کردن بکاپ خودکار"
        kb.add(types.InlineKeyboardButton(toggle_label, callback_data="adm:bkp:toggle"))
        kb.add(types.InlineKeyboardButton(f"⏰ زمان‌بندی: هر {interval} ساعت", callback_data="adm:bkp:interval"))
        kb.add(types.InlineKeyboardButton("📤 تنظیم مقصد", callback_data="adm:bkp:target"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            f"💾 <b>بکاپ</b>\n\n"
            f"بکاپ خودکار: {'🟢 فعال' if enabled == '1' else '🔴 غیرفعال'}\n"
            f"هر {interval} ساعت\n"
            f"مقصد: <code>{esc(target or 'ثبت نشده')}</code>",
            kb
        )
        return

    if data == "adm:bkp:manual":
        bot.answer_callback_query(call.id)
        _send_backup(uid)
        return

    if data == "adm:bkp:toggle":
        enabled = setting_get("backup_enabled", "0")
        setting_set("backup_enabled", "0" if enabled == "1" else "1")
        log_admin_action(uid, f"بکاپ خودکار {'غیرفعال' if enabled == '1' else 'فعال'} شد")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "admin:backup")
        return

    if data == "adm:bkp:interval":
        state_set(uid, "admin_set_backup_interval")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "⏰ بازه بکاپ خودکار را به ساعت وارد کنید (مثال: 6، 12، 24):",
                     back_button("admin:backup"))
        return

    if data == "adm:bkp:target":
        state_set(uid, "admin_set_backup_target")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📤 آیدی عددی کاربر یا کانال برای دریافت بکاپ را وارد کنید:",
                     back_button("admin:backup"))
        return

    if data == "adm:bkp:restore":
        state_set(uid, "admin_restore_backup")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "📥 <b>بازیابی بکاپ</b>\n\n"
            "⚠️ <b>توجه:</b> با بازیابی بکاپ، دیتابیس فعلی ربات حذف و با فایل بکاپ جایگزین می‌شود.\n\n"
            "فایل بکاپ (<code>.db</code>) را ارسال کنید:",
            back_button("admin:backup"))
        return

    # ── Admin: Payment approve/reject ─────────────────────────────────────────
    if data.startswith("adm:pay:ap:"):
        if not admin_has_perm(uid, "approve_payments"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        payment_id = int(data.split(":")[3])
        payment    = get_payment(payment_id)
        if not payment:
            bot.answer_callback_query(call.id, "تراکنش یافت نشد.", show_alert=True)
            return
        if payment["status"] not in ("pending",):
            bot.answer_callback_query(call.id, "این تراکنش قبلاً بررسی شده است.", show_alert=True)
            return
        user_row    = get_user(payment["user_id"])
        package_row = get_package(payment["package_id"]) if payment["package_id"] else None
        kind_label  = "شارژ کیف پول" if payment["kind"] == "wallet_charge" else "خرید کانفیگ"
        pkg_text    = ""
        if package_row:
            pkg_text = (
                f"\n🧩 نوع: {esc(package_row['type_name'])}"
                f"\n📦 پکیج: {esc(package_row['name'])}"
                f"\n🔋 حجم: {fmt_vol(package_row['volume_gb'])} | ⏰ {fmt_dur(package_row['duration_days'])}"
            )
        text = (
            f"✅ <b>تأیید تراکنش</b>\n\n"
            f"🧾 نوع: {kind_label}\n"
            f"👤 کاربر: {esc(user_row['full_name'] if user_row else '-')}\n"
            f"🆔 آیدی: <code>{payment['user_id']}</code>\n"
            f"💰 مبلغ: <b>{fmt_price(payment['amount'])}</b> تومان"
            f"{pkg_text}\n\n"
            f"📝 پیام برای کاربر را تایپ کنید، یا دکمه‌ی زیر را بزنید:"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ تأیید بدون توضیحات", callback_data=f"adm:pay:apc:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 انصراف", callback_data="nav:admin:panel"))
        state_set(uid, "admin_payment_approve_note", payment_id=payment_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:pay:apc:"):
        if not admin_has_perm(uid, "approve_payments"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        payment_id = int(data.split(":")[3])
        payment    = get_payment(payment_id)
        if not payment:
            bot.answer_callback_query(call.id, "تراکنش یافت نشد.", show_alert=True)
            return
        if payment["status"] not in ("pending",):
            bot.answer_callback_query(call.id, "این تراکنش قبلاً بررسی شده است.", show_alert=True)
            return
        state_clear(uid)
        finish_card_payment_approval(payment_id, "واریزی شما تأیید شد.", approved=True)
        bot.answer_callback_query(call.id, "✅ تأیید شد.")
        send_or_edit(call, "✅ تراکنش با موفقیت تأیید شد.", kb_admin_panel(uid))
        return

    if data.startswith("adm:pay:rj:"):
        if not admin_has_perm(uid, "approve_payments"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        payment_id = int(data.split(":")[3])
        payment    = get_payment(payment_id)
        if not payment:
            bot.answer_callback_query(call.id, "تراکنش یافت نشد.", show_alert=True)
            return
        if payment["status"] not in ("pending",):
            bot.answer_callback_query(call.id, "این تراکنش قبلاً بررسی شده است.", show_alert=True)
            return
        user_row   = get_user(payment["user_id"])
        package_row = get_package(payment["package_id"]) if payment["package_id"] else None
        kind_label = "شارژ کیف پول" if payment["kind"] == "wallet_charge" else "خرید کانفیگ"
        pkg_text   = ""
        if package_row:
            pkg_text = (
                f"\n🧩 نوع: {esc(package_row['type_name'])}"
                f"\n📦 پکیج: {esc(package_row['name'])}"
                f"\n🔋 حجم: {fmt_vol(package_row['volume_gb'])} | ⏰ {fmt_dur(package_row['duration_days'])}"
            )
        text = (
            f"❌ <b>رد تراکنش</b>\n\n"
            f"🧾 نوع: {kind_label}\n"
            f"👤 کاربر: {esc(user_row['full_name'] if user_row else '-')}\n"
            f"🆔 آیدی: <code>{payment['user_id']}</code>\n"
            f"💰 مبلغ: <b>{fmt_price(payment['amount'])}</b> تومان"
            f"{pkg_text}\n\n"
            f"📝 دلیل رد را تایپ کنید، یا دکمه‌ی زیر را بزنید:"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("❌ رد بدون توضیحات", callback_data=f"adm:pay:rjc:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 انصراف", callback_data="nav:admin:panel"))
        state_set(uid, "admin_payment_reject_note", payment_id=payment_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:pay:rjc:"):
        if not admin_has_perm(uid, "approve_payments"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        payment_id = int(data.split(":")[3])
        payment    = get_payment(payment_id)
        if not payment:
            bot.answer_callback_query(call.id, "تراکنش یافت نشد.", show_alert=True)
            return
        if payment["status"] not in ("pending",):
            bot.answer_callback_query(call.id, "این تراکنش قبلاً بررسی شده است.", show_alert=True)
            return
        state_clear(uid)
        finish_card_payment_approval(payment_id, "رسید شما رد شد.", approved=False)
        bot.answer_callback_query(call.id, "❌ رد شد.")
        send_or_edit(call, "❌ تراکنش رد شد.", kb_admin_panel(uid))
        return

    if data.startswith("adm:pending:addcfg:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        pending_id = int(data.split(":")[3])
        p_row = get_pending_order(pending_id)
        if not p_row:
            bot.answer_callback_query(call.id, "سفارش یافت نشد.", show_alert=True)
            return
        if p_row["status"] == "fulfilled":
            bot.answer_callback_query(call.id, "این سفارش قبلاً تکمیل شده است.", show_alert=True)
            return
        state_set(uid, "admin_pending_cfg_name", pending_id=pending_id)
        bot.answer_callback_query(call.id)
        pkg = get_package(p_row["package_id"])
        pkg_info = ""
        if pkg:
            pkg_info = (
                f"\n\n📦 <b>اطلاعات پکیج:</b>\n"
                f"🧩 نوع: {esc(pkg['type_name'])}\n"
                f"✏️ نام: {esc(pkg['name'])}\n"
                f"🔋 حجم: {fmt_vol(pkg['volume_gb'])}\n"
                f"⏰ مدت: {fmt_dur(pkg['duration_days'])}\n"
                f"💰 قیمت: {fmt_price(pkg['price'])} تومان"
            )
        send_or_edit(call,
            f"📝 <b>ثبت کانفیگ برای سفارش #{pending_id}</b>{pkg_info}\n\n"
            "لطفاً <b>نام سرویس</b> را ارسال کنید:",
            back_button("admin:panel"))
        return

    # ── 3x-ui Panel management ────────────────────────────────────────────────
    if data == "admin:panels":
        if not is_admin(uid) or not (uid in ADMIN_IDS or admin_has_perm(uid, "manage_panels")):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        _show_admin_panels(call)
        return

    if data == "adm:panel:add":
        if not is_admin(uid) or not (uid in ADMIN_IDS or admin_has_perm(uid, "manage_panels")):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "panel_add_name")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🖥 <b>Register Panel Config</b>\n\nStep 1/5: Enter Panel <b>Name</b>:",
            back_button("admin:panels"))
        return

    if data.startswith("adm:panel:pkgs:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        panel_id = int(data.split(":")[3])
        bot.answer_callback_query(call.id)
        _show_panel_packages(call, panel_id)
        return

    if data.startswith("adm:panel:pkadd:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        panel_id = int(data.split(":")[3])
        state_set(uid, "panel_pkg_add_name", panel_id=panel_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "📦 <b>Add Traffic Package</b>\n\nStep 1/3: Enter Package <b>Name</b>:",
            back_button("admin:panels"))
        return

    if data.startswith("adm:panel:pkdel:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        pp_id = int(data.split(":")[3])
        pp = get_panel_package(pp_id)
        if not pp:
            bot.answer_callback_query(call.id, "Package not found.", show_alert=True)
            return
        delete_panel_package(pp_id)
        bot.answer_callback_query(call.id, "✅ Package deleted.")
        log_admin_action(uid, f"پکیج پنل #{pp_id} از پنل #{pp['panel_id']} حذف شد")
        _show_panel_packages(call, pp["panel_id"])
        return

    if data.startswith("adm:panel:edit:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        panel_id = int(data.split(":")[3])
        bot.answer_callback_query(call.id)
        _show_panel_edit(call, panel_id)
        return

    if data.startswith("adm:panel:ef:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        parts    = data.split(":")
        field    = parts[3]
        panel_id = int(parts[4])
        state_set(uid, "panel_edit_field", field=field, panel_id=panel_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"✏️ Enter new value for <b>{field}</b>:",
            back_button("admin:panels"))
        return

    if data.startswith("adm:panel:toggle:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        parts    = data.split(":")
        panel_id = int(parts[3])
        new_val  = int(parts[4])
        update_panel_field(panel_id, "is_active", new_val)
        log_admin_action(uid, f"پنل #{panel_id} {'فعال' if new_val else 'غیرفعال'} شد")
        bot.answer_callback_query(call.id, "✅ Updated.")
        _show_panel_edit(call, panel_id)
        return

    if data.startswith("adm:panel:del:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        panel_id = int(data.split(":")[3])
        panel    = get_panel(panel_id)
        if not panel:
            bot.answer_callback_query(call.id, "Panel not found.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"adm:panel:delok:{panel_id}"),
            types.InlineKeyboardButton("❌ Cancel",      callback_data="admin:panels"),
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"⚠️ Delete panel <b>{esc(panel['name'])}</b>?\n"
            "All packages and jobs linked to it will also be removed.", kb)
        return

    if data.startswith("adm:panel:delok:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        panel_id = int(data.split(":")[3])
        delete_panel(panel_id)
        bot.answer_callback_query(call.id, "✅ Panel deleted.")
        log_admin_action(uid, f"پنل #{panel_id} حذف شد")
        _show_admin_panels(call)
        return

    if data == "adm:panel:api_settings":
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        current_key     = setting_get("worker_api_key", "")
        current_port    = setting_get("worker_api_port", "8080")
        current_enabled = setting_get("worker_api_enabled", "0")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔑 Set API Key",  callback_data="adm:panel:set_api_key"))
        kb.add(types.InlineKeyboardButton("🔌 Set API Port", callback_data="adm:panel:set_api_port"))
        toggle_lbl = "🔴 Disable API" if current_enabled == "1" else "🟢 Enable API"
        new_enabled = "0" if current_enabled == "1" else "1"
        kb.add(types.InlineKeyboardButton(toggle_lbl, callback_data=f"adm:panel:api_toggle:{new_enabled}"))
        kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin:panels"))
        bot.answer_callback_query(call.id)
        masked_key = (current_key[:6] + "…") if current_key else "(not set)"
        send_or_edit(call,
            "⚙️ <b>Worker API Settings</b>\n\n"
            f"🔑 API Key: <code>{masked_key}</code>\n"
            f"🔌 Port: <code>{current_port}</code>\n"
            f"Status: {'🟢 Enabled' if current_enabled == '1' else '🔴 Disabled'}\n\n"
            "Share the API Key with your Iran Worker's config.env",
            kb)
        return

    if data.startswith("adm:panel:api_toggle:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        new_val = data.split(":")[3]
        setting_set("worker_api_enabled", new_val)
        log_admin_action(uid, f"Worker API {'فعال' if new_val == '1' else 'غیرفعال'} شد")
        bot.answer_callback_query(call.id, "✅ Updated.")
        _fake_call(call, "adm:panel:api_settings")
        return

    if data == "adm:panel:set_api_key":
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "panel_set_api_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔑 Enter new <b>Worker API Key</b>:\n(min 16 characters, letters and digits only)",
            back_button("admin:panels"))
        return

    if data == "adm:panel:set_api_port":
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "panel_set_api_port")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔌 Enter new <b>API Server Port</b> (default 8080):",
            back_button("admin:panels"))
        return

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

