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
    esc, fmt_price, now_str, display_name, display_username, safe_support_url,
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
    get_payment, create_payment, approve_payment, reject_payment, complete_payment,
    get_agency_price, set_agency_price,
    get_all_admin_users, get_admin_user, add_admin_user, update_admin_permissions, remove_admin_user,
    get_all_panels, get_panel, add_panel, delete_panel,
    get_panel_packages, add_panel_package, delete_panel_package, update_panel_field,
    get_conn, create_pending_order, get_pending_order, add_config, search_users,
    reset_all_free_tests,
)
from ..gateways.base import is_gateway_available, is_card_info_complete
from ..gateways.crypto import fetch_crypto_prices
from ..gateways.tetrapay import create_tetrapay_order, verify_tetrapay_order
from ..gateways.swapwallet import (
    create_swapwallet_invoice, check_swapwallet_invoice,
    show_swapwallet_page, swapwallet_error_page,
)
from ..gateways.swapwallet_crypto import (
    create_swapwallet_crypto_invoice, check_swapwallet_crypto_invoice,
    show_swapwallet_crypto_page,
)
from ..ui.helpers import send_or_edit, check_channel_membership, channel_lock_message
from ..ui.keyboards import kb_main, kb_admin_panel
from ..ui.menus import show_main_menu, show_profile, show_support, show_my_configs
from ..ui.notifications import (
    deliver_purchase_message, admin_purchase_notify, admin_renewal_notify,
    notify_pending_order_to_admins, _complete_pending_order, auto_fulfill_pending_orders,
)
from ..group_manager import (
    ensure_group_topics, reset_and_recreate_topics, get_group_id,
    _count_active_topics, TOPICS, send_to_topic,
)
from ..group_manager import (
    ensure_group_topics, reset_and_recreate_topics, get_group_id, _count_active_topics, TOPICS,
    send_to_topic,
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


@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid  = call.from_user.id
    ensure_user(call.from_user)
    data = call.data or ""

    # Channel check button
    if data == "check_channel":
        if check_channel_membership(uid):
            bot.answer_callback_query(call.id, "✅ عضویت تأیید شد!")
            show_main_menu(call)
        else:
            bot.answer_callback_query(call.id, "❌ هنوز عضو کانال نشده‌اید.", show_alert=True)
        return

    if not check_channel_membership(uid):
        bot.answer_callback_query(call.id)
        channel_lock_message(call)
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
                update_balance(uid, payment["amount"])
                complete_payment(payment_id)
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
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ تأیید", callback_data=f"agency:approve:{uid}"),
            types.InlineKeyboardButton("❌ رد", callback_data=f"agency:reject:{uid}"),
        )
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, text, reply_markup=kb)
            except Exception:
                pass
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
        bot.answer_callback_query(call.id, "✅ نمایندگی تأیید شد.")
        _show_admin_user_detail(call, target_uid)
        try:
            bot.send_message(target_uid, "🎉 <b>درخواست نمایندگی شما تأیید شد!</b>\n\nاکنون شما نماینده هستید.")
        except Exception:
            pass
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
            title = f"{p['name']} | {p['volume_gb']} گیگ | {p['duration_days']} روز | {fmt_price(price)} ت"
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
        text = (
            "♻️ <b>تمدید سرویس</b>\n\n"
            f"🔮 سرویس فعلی: {esc(urllib.parse.unquote(item['service_name'] or ''))}\n"
            f"📦 پکیج تمدید: {esc(package_row['name'])}\n"
            f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
            f"⏰ مدت: {package_row['duration_days']} روز\n"
            f"💰 قیمت: {fmt_price(price)} تومان\n\n"
            "روش پرداخت را انتخاب کنید:"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💰 پرداخت از موجودی", callback_data=f"rpay:wallet:{purchase_id}:{package_id}"))
        if is_gateway_available("card", uid, price) and is_card_info_complete():
            kb.add(types.InlineKeyboardButton("💳 کارت به کارت", callback_data=f"rpay:card:{purchase_id}:{package_id}"))
        if is_gateway_available("crypto", uid, price):
            kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال", callback_data=f"rpay:crypto:{purchase_id}:{package_id}"))
        if is_gateway_available("tetrapay", uid, price):
            kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data=f"rpay:tetrapay:{purchase_id}:{package_id}"))
        if is_gateway_available("swapwallet", uid, price):
            kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین ریالی (SwapWallet)", callback_data=f"rpay:swapwallet:{purchase_id}:{package_id}"))
        if is_gateway_available("swapwallet_crypto", uid, price):
            kb.add(types.InlineKeyboardButton("💎 پرداخت کریپتو (SwapWallet)", callback_data=f"rpay:swapwallet_crypto:{purchase_id}:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"renew:{purchase_id}"))
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
        state_set(uid, "renew_crypto_select_coin", package_id=package_id, amount=price,
                  purchase_id=purchase_id, config_id=item["config_id"])
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=price)
        return

    if data.startswith("rpay:swapwallet:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        success, inv = check_swapwallet_invoice(invoice_id)
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
                admin_renewal_notify(uid, item, package_row, payment["amount"], "SwapWallet")
            state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("rpay:swapwallet:"):
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
        price    = get_effective_price(uid, package_row)
        order_id = f"rnw-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_swapwallet_invoice(price, order_id, f"تمدید {package_row['name']}")
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            _swapwallet_error_inline(call, err_msg)
            return
        invoice_id    = result.get("id", "")
        payment_links = result.get("paymentLinks", [])
        payment_id = create_payment("renewal", uid, package_id, price, "swapwallet", status="pending",
                                     config_id=item["config_id"])
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_renewal_swapwallet_verify", payment_id=payment_id, invoice_id=invoice_id,
                  purchase_id=purchase_id)
        show_swapwallet_page(call, amount_toman=price, invoice_id=invoice_id,
                             payment_links=payment_links, payment_id=payment_id,
                             verify_cb=f"rpay:swapwallet:verify:{payment_id}")
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
                cfg_row = conn.execute("SELECT service_name FROM configs WHERE id=?", (config_id,)).fetchone()
            svc_name = urllib.parse.unquote(cfg_row["service_name"] or "") if cfg_row else ""
            bot.send_message(target_uid,
                f"🎉 <b>تمدید سرویس انجام شد!</b>\n\n"
                f"✅ سرویس <b>{esc(svc_name)}</b> شما با موفقیت تمدید شد.\n"
                "از اعتماد شما سپاسگزاریم. 🙏")
        except Exception:
            pass
        try:
            send_to_topic("renewal_log",
                f"🔄 <b>تمدید تأیید شد</b>\n\n"
                f"🔢 آیدی کاربر: <code>{target_uid}</code>\n"
                f"🔮 سرویس: {esc(svc_name or str(config_id))}"
            )
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
            title = f"{p['name']}{stock_tag} | {p['volume_gb']} گیگ | {p['duration_days']} روز | {fmt_price(price)} ت"
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
        text = (
            "💳 <b>انتخاب روش پرداخت</b>\n\n"
            f"🧩 نوع: {esc(package_row['type_name'])}\n"
            f"📦 پکیج: {esc(package_row['name'])}\n"
            f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
            f"⏰ مدت: {package_row['duration_days']} روز\n"
            f"💰 قیمت: {fmt_price(price)} تومان\n\n"
            "روش پرداخت را انتخاب کنید:"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💰 پرداخت از موجودی", callback_data=f"pay:wallet:{package_id}"))
        if is_gateway_available("card", uid, price) and is_card_info_complete():
            kb.add(types.InlineKeyboardButton("💳 کارت به کارت", callback_data=f"pay:card:{package_id}"))
        if is_gateway_available("crypto", uid, price):
            kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال", callback_data=f"pay:crypto:{package_id}"))
        if is_gateway_available("tetrapay", uid, price):
            kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data=f"pay:tetrapay:{package_id}"))
        if is_gateway_available("swapwallet", uid, price):
            kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین ریالی (SwapWallet)", callback_data=f"pay:swapwallet:{package_id}"))
        if is_gateway_available("swapwallet_crypto", uid, price):
            kb.add(types.InlineKeyboardButton("💎 پرداخت کریپتو (SwapWallet)", callback_data=f"pay:swapwallet_crypto:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"buy:t:{package_row['type_id']}"))
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
        purchase_id = assign_config_to_user(config_id, uid, package_id, price, "wallet", is_test=0)
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

    # ── SwapWallet ────────────────────────────────────────────────────────────
    if data.startswith("pay:swapwallet:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        success, inv = check_swapwallet_invoice(invoice_id)
        if not success:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        if inv.get("status") in ("PAID", "COMPLETED") or inv.get("paidAt"):
            if payment["kind"] == "wallet_charge":
                update_balance(uid, payment["amount"])
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, f"✅ پرداخت شما تأیید و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان", back_button("main"))
                state_clear(uid)
            else:
                config_id  = payment["config_id"]
                package_id = payment["package_id"]
                package_row = get_package(package_id)
                if not config_id:
                    config_id = reserve_first_config(package_id, payment_id)
                if not config_id:
                    pending_id = create_pending_order(uid, package_id, payment_id, payment["amount"], "swapwallet")
                    complete_payment(payment_id)
                    bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                    send_or_edit(call,
                        "✅ پرداخت شما تأیید شد.\n\n"
                        "⚠️ <b>موجودی تحویل فوری ربات به اتمام رسید.</b>\n"
                        "درخواست شما برای ادمین ارسال شد. در کمترین فرصت کانفیگ شما تحویل داده می‌شود.\n"
                        "🙏 از صبر شما متشکریم.", back_button("main"))
                    notify_pending_order_to_admins(pending_id, uid, package_row, payment["amount"], "swapwallet")
                    state_clear(uid)
                    return
                purchase_id = assign_config_to_user(config_id, uid, package_id, payment["amount"], "swapwallet", is_test=0)
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, "✅ پرداخت شما تأیید شد و سرویس آماده است.", back_button("main"))
                deliver_purchase_message(call.message.chat.id, purchase_id)
                admin_purchase_notify("SwapWallet", get_user(uid), package_row)
                state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("pay:swapwallet:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or (setting_get("preorder_mode", "0") == "1" and package_row["stock"] <= 0):
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price    = get_effective_price(uid, package_row)
        order_id = f"cfg-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_swapwallet_invoice(price, order_id, f"خرید {package_row['name']}")
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            _swapwallet_error_inline(call, err_msg)
            return
        invoice_id    = result.get("id", "")
        payment_links = result.get("paymentLinks", [])
        payment_id = create_payment("config_purchase", uid, package_id, price, "swapwallet", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_swapwallet_verify", payment_id=payment_id, invoice_id=invoice_id)
        show_swapwallet_page(call, amount_toman=price, invoice_id=invoice_id,
                             payment_links=payment_links, payment_id=payment_id,
                             verify_cb=f"pay:swapwallet:verify:{payment_id}")
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
                update_balance(uid, payment["amount"])
                complete_payment(payment_id)
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
        purchase_id = assign_config_to_user(config_id, uid, package_row["id"], 0, "free_test", is_test=1)
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

    if data == "wallet:charge:swapwallet":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        order_id = f"wallet-{uid}-{int(datetime.now().timestamp())}"
        success, result = create_swapwallet_invoice(amount, order_id, "شارژ کیف پول")
        if not success:
            err_msg = result.get("error", "خطای ناشناخته") if isinstance(result, dict) else str(result)
            _swapwallet_error_inline(call, err_msg)
            return
        invoice_id    = result.get("id", "")
        payment_links = result.get("paymentLinks", [])
        payment_id = create_payment("wallet_charge", uid, None, amount, "swapwallet", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_swapwallet_verify", payment_id=payment_id, invoice_id=invoice_id)
        show_swapwallet_page(call, amount_toman=amount, invoice_id=invoice_id,
                             payment_links=payment_links, payment_id=payment_id,
                             verify_cb=f"pay:swapwallet:verify:{payment_id}")
        return

    # ── SwapWallet Crypto (network selection) ─────────────────────────────────
    if data == "wallet:charge:swapwallet_crypto":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
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
                update_balance(uid, payment["amount"])
                complete_payment(payment_id)
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
            f"حجم: {package_row['volume_gb']} GB\n"
            f"مدت: {package_row['duration_days']} روز\n"
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
                f"{p['name']} | {p['volume_gb']} گیگ | {p['duration_days']} روز",
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
                      has_inquiry=s["has_inquiry"], count=s["count"], prefix="")
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
            count = s.get("count", 0)
            prefix = s.get("prefix", "")
            state_set(uid, "admin_bulk_data",
                      package_id=s["package_id"], type_id=s["type_id"],
                      has_inquiry=has_inq, count=count, prefix=prefix, suffix="")
            bot.answer_callback_query(call.id)
            if has_inq:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    f"تعداد: <b>{count}</b> کانفیگ\n\n"
                    "هر کانفیگ <b>دو خط</b> دارد:\n"
                    "خط اول: لینک کانفیگ\n"
                    "خط دوم: لینک استعلام (شروع با http)\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "http://panel.com/sub/1\n"
                    "vless://def...#name2\n"
                    "http://panel.com/sub/2</code>"
                )
            else:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    f"تعداد: <b>{count}</b> کانفیگ\n\n"
                    "هر خط یک لینک کانفیگ:\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "vless://def...#name2</code>"
                )
            send_or_edit(call, fmt_text, back_button("admin:add_config"))
            return

        # Inquiry yes/no
        if rest.startswith("inq:"):
            sub_parts = rest.split(":")
            yn = sub_parts[1]
            pkg_id = int(sub_parts[2])
            has_inq = (yn == "y")
            state_set(uid, "admin_bulk_count",
                      package_id=pkg_id, type_id=state_data(uid).get("type_id", 0),
                      has_inquiry=has_inq)
            bot.answer_callback_query(call.id)
            send_or_edit(call, "🔢 تعداد کانفیگ‌ها را وارد کنید:", back_button("admin:add_config"))
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
            kb.add(types.InlineKeyboardButton(f"{mark} {c['service_name']}", callback_data=f"adm:stk:cfg:{c['id']}"))
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
            kb.add(types.InlineKeyboardButton(f"{mark} {c['service_name']}", callback_data=f"adm:stk:cfg:{c['id']}"))
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
            f"🔋 حجم: {row['volume_gb']} گیگ\n"
            f"⏰ مدت: {row['duration_days']} روز\n\n"
            f"💝 Config:\n<code>{esc(row['config_text'])}</code>\n\n"
            f"🔋 Volume web: {esc(row['inquiry_link'] or '-')}\n"
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
        kb.add(types.InlineKeyboardButton("🗑 حذف کانفیگ", callback_data=f"adm:stk:del:{config_id}:{row['package_id']}"))
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

    if data.startswith("adm:stk:exp:"):
        parts = data.split(":")
        config_id  = int(parts[3])
        package_id = int(parts[4]) if len(parts) > 4 else 0
        expire_config(config_id)
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

        if sub == "sts":  # toggle status
            user = get_user(target_id)
            new_status = "unsafe" if user["status"] == "safe" else "safe"
            set_user_status(target_id, new_status)
            label = "ناامن" if new_status == "unsafe" else "امن"
            bot.answer_callback_query(call.id, f"وضعیت کاربر به {label} تغییر کرد.")
            _show_admin_user_detail(call, target_id)
            return

        if sub == "ag":  # toggle agent
            user     = get_user(target_id)
            new_flag = 0 if user["is_agent"] else 1
            set_user_agent(target_id, new_flag)
            label = "فعال" if new_flag else "غیرفعال"
            bot.answer_callback_query(call.id, f"نمایندگی {label} شد.")
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
                    kb.add(types.InlineKeyboardButton(
                        f"{p['service_name']}{expired_mark}",
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
            kb.add(types.InlineKeyboardButton(c["service_name"],
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

    # ── Admin: Broadcast ──────────────────────────────────────────────────────
    if data == "admin:broadcast":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📣 همه کاربران",  callback_data="adm:bc:all"))
        kb.add(types.InlineKeyboardButton("🛍 فقط مشتریان", callback_data="adm:bc:cust"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",       callback_data="admin:panel"))
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
        send_or_edit(call, f"🛠 <b>ساخت تاپیک</b>\n\n{result}", back_button("admin:group"))
        return

    if data == "adm:grp:reset":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "در حال بازسازی...", show_alert=False)
        result = reset_and_recreate_topics()
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
        kb.add(types.InlineKeyboardButton("🏢 مدیریت گروه",    callback_data="admin:group"))
        kb.add(types.InlineKeyboardButton("💾 بکاپ",            callback_data="admin:backup"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",        callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "⚙️ <b>تنظیمات</b>", kb)
        return

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
        bot.answer_callback_query(call.id, "تنظیم فروش بر اساس موجودی تغییر کرد.")
        from types import SimpleNamespace as _SN
        fake = _SN(id=call.id, from_user=call.from_user, message=call.message, data="adm:set:shop")
        _dispatch_callback(fake, uid, "adm:set:shop")
        return

    # ── Gateway settings ─────────────────────────────────────────────────────
    if data == "adm:set:gateways":
        kb = types.InlineKeyboardMarkup()
        for gw_key, gw_label in [
            ("card",             "💳 کارت به کارت"),
            ("crypto",           "💎 ارز دیجیتال"),
            ("tetrapay",         "🏦 کارت به کارت آنلاین (TetraPay)"),
            ("swapwallet",       "🏦 پرداخت آنلاین ریالی (SwapWallet)"),
            ("swapwallet_crypto","💎 پرداخت کریپتو (SwapWallet)"),
        ]:
            enabled = setting_get(f"gw_{gw_key}_enabled", "0")
            status_icon = "🟢" if enabled == "1" else "🔴"
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
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        range_label = "🟢 فعال" if range_enabled == "1" else "🔴 غیرفعال"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:card:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:card:vis"),
        )
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label}", callback_data="adm:gw:card:range"))
        kb.add(types.InlineKeyboardButton("💳 شماره کارت", callback_data="adm:set:card"))
        kb.add(types.InlineKeyboardButton("🏦 نام بانک", callback_data="adm:set:bank"))
        kb.add(types.InlineKeyboardButton("👤 نام صاحب کارت", callback_data="adm:set:owner"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        text = (
            "💳 <b>درگاه کارت به کارت</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"کارت: <code>{esc(card or 'ثبت نشده')}</code>\n"
            f"بانک: {esc(bank or 'ثبت نشده')}\n"
            f"صاحب: {esc(owner or 'ثبت نشده')}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:card:toggle":
        enabled = setting_get("gw_card_enabled", "0")
        setting_set("gw_card_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:card")
        return

    if data == "adm:gw:card:vis":
        vis = setting_get("gw_card_visibility", "public")
        setting_set("gw_card_visibility", "secure" if vis == "public" else "public")
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
        for coin_key, coin_label in CRYPTO_COINS:
            addr = setting_get(f"crypto_{coin_key}", "")
            status_icon = "✅" if addr else "❌"
            kb.add(types.InlineKeyboardButton(f"{status_icon} {coin_label}", callback_data=f"adm:set:cw:{coin_key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        text = (
            "💎 <b>درگاه ارز دیجیتال</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            "برای ویرایش آدرس ولت روی هر ارز بزنید:"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:crypto:toggle":
        enabled = setting_get("gw_crypto_enabled", "0")
        setting_set("gw_crypto_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:crypto")
        return

    if data == "adm:gw:crypto:vis":
        vis = setting_get("gw_crypto_visibility", "public")
        setting_set("gw_crypto_visibility", "secure" if vis == "public" else "public")
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
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API", callback_data="adm:set:tetrapay_key"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سایت TetraPay", url="https://tetra98.com"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        if api_key:
            key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>"
        else:
            key_display = "❌ <b>ثبت نشده</b> — ابتدا از سایت TetraPay کلید API خود را دریافت کنید"
        text = (
            "🏦 <b>درگاه کارت به کارت آنلاین (TetraPay)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"💳 پرداخت از تلگرام: {bot_label}\n"
            f"🌐 پرداخت از مرورگر: {web_label}\n\n"
            f"کلید API: {key_display}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:tetrapay:toggle":
        enabled = setting_get("gw_tetrapay_enabled", "0")
        setting_set("gw_tetrapay_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:vis":
        vis = setting_get("gw_tetrapay_visibility", "public")
        setting_set("gw_tetrapay_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:mode_bot":
        cur = setting_get("tetrapay_mode_bot", "1")
        setting_set("tetrapay_mode_bot", "0" if cur == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:mode_web":
        cur = setting_get("tetrapay_mode_web", "1")
        setting_set("tetrapay_mode_web", "0" if cur == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:set:tetrapay_key":
        state_set(uid, "admin_set_tetrapay_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API تتراپی را ارسال کنید:", back_button("adm:set:gw:tetrapay"))
        return

    if data == "adm:set:gw:swapwallet":
        enabled  = setting_get("gw_swapwallet_enabled", "0")
        vis      = setting_get("gw_swapwallet_visibility", "public")
        api_key  = setting_get("swapwallet_api_key", "")
        username = setting_get("swapwallet_username", "")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label     = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:swapwallet:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}",    callback_data="adm:gw:swapwallet:vis"),
        )
        range_enabled_sw = setting_get("gw_swapwallet_range_enabled", "0")
        range_label_sw = "🟢 فعال" if range_enabled_sw == "1" else "🔴 غیرفعال"
        kb.add(types.InlineKeyboardButton(f"📊 بازه پرداختی: {range_label_sw}", callback_data="adm:gw:swapwallet:range"))
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API",             callback_data="adm:set:swapwallet_key"))
        kb.add(types.InlineKeyboardButton("👤 نام کاربری فروشگاه",          callback_data="adm:set:swapwallet_username"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سواپ ولت", url="https://swapwallet.app"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>" if api_key else "➖ نیاز نیست (اختیاری)"
        user_status = "✅ ثبت شده" if username else "❌ ثبت نشده"
        text = (
            "🏦 <b>درگاه پرداخت آنلاین ریالی (SwapWallet)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"👤 نام کاربری Application: <code>{esc(username or 'ثبت نشده')}</code> {user_status}\n"
            f"🔑 کلید API: {key_display}\n\n"
            "⚠️ <b>مهم — پیش‌نیاز:</b>\n"
            "این درگاه نیاز به یک <b>Application</b> (کسب‌وکار) جداگانه\n"
            "در SwapWallet دارد، نه اکانت شخصی.\n\n"
            "📖 <b>مراحل راه‌اندازی:</b>\n"
            "۱. ربات @SwapWalletBot را باز کنید\n"
            "۲. به بخش <b>کسب‌وکار / Business</b> بروید\n"
            "۳. یک کسب‌وکار جدید بسازید\n"
            "۴. <b>نام کاربری همان کسب‌وکار</b> را اینجا وارد کنید\n"
            "۵. کلید API اختیاری است (برای این درگاه نیاز نیست)"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:swapwallet:toggle":
        enabled = setting_get("gw_swapwallet_enabled", "0")
        setting_set("gw_swapwallet_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet")
        return

    if data == "adm:gw:swapwallet:vis":
        vis = setting_get("gw_swapwallet_visibility", "public")
        setting_set("gw_swapwallet_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet")
        return

    if data == "adm:set:swapwallet_key":
        state_set(uid, "admin_set_swapwallet_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API سواپ ولت را ارسال کنید (مثال: <code>apikey-xxx...</code>):", back_button("adm:set:gw:swapwallet"))
        return

    if data == "adm:set:swapwallet_username":
        state_set(uid, "admin_set_swapwallet_username")
        bot.answer_callback_query(call.id)
        current = setting_get("swapwallet_username", "")
        send_or_edit(call,
            f"👤 نام کاربری فروشگاه سواپ ولت را ارسال کنید.\n"
            f"مقدار فعلی: <code>{esc(current or 'ثبت نشده')}</code>",
            back_button("adm:set:gw:swapwallet"))
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
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سواپ ولت", url="https://swapwallet.app"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>" if api_key else "❌ <b>ثبت نشده — الزامی</b>"
        user_status = "✅ ثبت شده" if username else "❌ ثبت نشده"
        text = (
            "💎 <b>درگاه پرداخت کریپتو (SwapWallet)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"👤 نام کاربری Application: <code>{esc(username or 'ثبت نشده')}</code> {user_status}\n"
            f"🔑 کلید API: {key_display}\n\n"
            "📖 <b>شبکه‌های پشتیبانی:</b> TRON · TON · BSC\n\n"
            "⚠️ <b>مهم — پیش‌نیاز:</b>\n"
            "این درگاه نیاز به یک <b>Application</b> (کسب‌وکار) جداگانه\n"
            "در SwapWallet دارد، نه اکانت شخصی.\n\n"
            "📖 <b>مراحل راه‌اندازی:</b>\n"
            "۱. ربات @SwapWalletBot را باز کنید\n"
            "۲. به بخش <b>کسب‌وکار / Business</b> بروید\n"
            "۳. یک کسب‌وکار جدید بسازید\n"
            "۴. <b>نام کاربری همان کسب‌وکار</b> را اینجا وارد کنید\n"
            "۵. از پروفایل ← کلید API کلید بگیرید و وارد کنید"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:swapwallet_crypto:toggle":
        enabled = setting_get("gw_swapwallet_crypto_enabled", "0")
        setting_set("gw_swapwallet_crypto_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet_crypto")
        return

    if data == "adm:gw:swapwallet_crypto:vis":
        vis = setting_get("gw_swapwallet_crypto_visibility", "public")
        setting_set("gw_swapwallet_crypto_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet_crypto")
        return

    if data == "adm:set:swapwallet_crypto_key":
        state_set(uid, "admin_set_swapwallet_crypto_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API سواپ ولت (کریپتو) را ارسال کنید (مثال: <code>apikey-xxx...</code>):",
                     back_button("adm:set:gw:swapwallet_crypto"))
        return

    if data == "adm:set:swapwallet_crypto_username":
        state_set(uid, "admin_set_swapwallet_crypto_username")
        bot.answer_callback_query(call.id)
        current = setting_get("swapwallet_crypto_username", "")
        send_or_edit(call,
            f"👤 نام کاربری فروشگاه سواپ ولت (کریپتو) را ارسال کنید.\n"
            f"مقدار فعلی: <code>{esc(current or 'ثبت نشده')}</code>",
            back_button("adm:set:gw:swapwallet_crypto"))
        return

    _GW_RANGE_LABELS = {"card": "💳 کارت به کارت", "crypto": "💎 ارز دیجیتال", "tetrapay": "🏦 TetraPay", "swapwallet": "💎 SwapWallet", "swapwallet_crypto": "💎 SwapWallet کریپتو"}

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
                f"\n🔋 حجم: {package_row['volume_gb']} گیگ | ⏰ {package_row['duration_days']} روز"
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
                f"\n🔋 حجم: {package_row['volume_gb']} گیگ | ⏰ {package_row['duration_days']} روز"
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
                f"🔋 حجم: {pkg['volume_gb']} گیگ\n"
                f"⏰ مدت: {pkg['duration_days']} روز\n"
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

