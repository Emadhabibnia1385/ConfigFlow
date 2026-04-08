# -*- coding: utf-8 -*-
import os
import traceback
import sqlite3
import urllib.parse
from datetime import datetime
from telebot import types
from ..config import ADMIN_IDS, ADMIN_PERMS, PERM_FULL_SET, CONFIGS_PER_PAGE, DB_NAME
from ..bot_instance import bot
from ..helpers import (
    esc, fmt_price, fmt_vol, fmt_dur, now_str, display_name, display_username,
    is_admin, admin_has_perm, back_button,
    state_set, state_clear, state_name, state_data, parse_int, parse_volume, normalize_text_number,
)
from ..db import (
    setting_get, setting_set,
    ensure_user, get_user, get_users, set_user_status,
    set_user_agent, update_balance, get_user_purchases, get_purchase,
    get_all_types, get_packages, get_package, add_package, update_package_field, delete_package,
    add_type, update_type, update_type_description, delete_type,
    get_registered_packages_stock, get_configs_paginated, count_configs,
    expire_config, add_config,
    assign_config_to_user, reserve_first_config,
    get_payment, create_payment, approve_payment, reject_payment, complete_payment,
    update_payment_receipt,
    get_agency_price, set_agency_price,
    get_agency_price_config, set_agency_price_config,
    get_agency_type_discount, set_agency_type_discount,
    get_all_admin_users, get_admin_user, add_admin_user, update_admin_permissions, remove_admin_user,
    get_all_panels, get_panel, add_panel, delete_panel,
    get_panel_packages, add_panel_package, delete_panel_package, update_panel_field,
    get_conn, create_pending_order, get_pending_order, search_users,
    notify_first_start_if_needed, update_config_field,
    add_pinned_message, update_pinned_message,
    save_pinned_send, get_pinned_sends,
)
from ..gateways.base import is_gateway_available, is_card_info_complete
from ..gateways.tetrapay import create_tetrapay_order, verify_tetrapay_order
from ..ui.helpers import send_or_edit, check_channel_membership, channel_lock_message
from ..ui.keyboards import kb_main, kb_admin_panel
from ..ui.menus import show_main_menu, show_profile, show_support, show_my_configs
from ..ui.notifications import (
    deliver_purchase_message, admin_purchase_notify, admin_renewal_notify,
    notify_pending_order_to_admins, _complete_pending_order, auto_fulfill_pending_orders,
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

@bot.message_handler(content_types=["text", "photo", "document"])
def universal_handler(message):
    uid    = message.from_user.id
    ensure_user(message.from_user)

    # Restricted user check
    _u = get_user(uid)
    if _u and _u["status"] == "restricted" and not is_admin(uid):
        bot.send_message(
            message.chat.id,
            "🚫 <b>دسترسی محدود شده</b>\n\n"
            "شما از ربات محدود شده‌اید و نمی‌توانید از آن استفاده کنید.\n"
            "در صورت نیاز با پشتیبانی تماس بگیرید.",
            parse_mode="HTML"
        )
        return

    # Bot status check for non-admins
    if not is_admin(uid):
        _bot_status = setting_get("bot_status", "on")
        if _bot_status == "off":
            return
        if _bot_status == "update":
            bot.send_message(
                message.chat.id,
                "🔄 <b>ربات در حال بروزرسانی است</b>\n\n"
                "فعلاً ربات در حال بروزرسانی می‌باشد، لطفاً بعداً اقدام نمایید. 🙏\n\n"
                "در صورتی که کار فوری دارید، می‌توانید با پشتیبانی در ارتباط باشید.",
                parse_mode="HTML"
            )
            return

    # Channel check
    if not check_channel_membership(uid):
        channel_lock_message(message)
        return

    sn = state_name(uid)
    sd = state_data(uid)

    try:
        # ── Broadcast ─────────────────────────────────────────────────────────
        def _bc_send(target_id):
            """Forward if admin forwarded something, copy if admin wrote directly."""
            if message.forward_date:
                bot.forward_message(target_id, message.chat.id, message.message_id)
            else:
                bot.copy_message(target_id, message.chat.id, message.message_id)

        if sn == "admin_broadcast_all" and is_admin(uid):
            users = get_users()
            sent  = 0
            for u in users:
                try:
                    _bc_send(u["user_id"])
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} کاربر ارسال شد.", reply_markup=kb_admin_panel())
            from ..group_manager import send_to_topic as _stt
            _stt("broadcast_report", f"📢 <b>اطلاع‌رسانی (همه کاربران)</b>\n\n✅ برای {sent} کاربر ارسال شد.")
            return

        if sn == "admin_broadcast_customers" and is_admin(uid):
            users = get_users(has_purchase=True)
            sent  = 0
            for u in users:
                try:
                    _bc_send(u["user_id"])
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} مشتری ارسال شد.", reply_markup=kb_admin_panel())
            from ..group_manager import send_to_topic as _stt
            _stt("broadcast_report", f"📢 <b>اطلاع‌رسانی (مشتریان)</b>\n\n✅ برای {sent} مشتری ارسال شد.")
            return

        if sn == "admin_broadcast_normal" and is_admin(uid):
            from ..db import get_all_admin_users as _get_admins
            admin_ids_set = set(ADMIN_IDS)
            for _ar in _get_admins():
                admin_ids_set.add(_ar["user_id"])
            users = get_users(has_purchase=True)
            sent  = 0
            for u in users:
                if u["user_id"] in admin_ids_set:
                    continue
                if u["is_agent"]:
                    continue
                try:
                    _bc_send(u["user_id"])
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} مشتری عادی ارسال شد.", reply_markup=kb_admin_panel())
            from ..group_manager import send_to_topic as _stt
            _stt("broadcast_report", f"📢 <b>اطلاع‌رسانی (مشتریان عادی)</b>\n\n✅ برای {sent} کاربر ارسال شد.")
            return

        if sn == "admin_broadcast_agents" and is_admin(uid):
            users = get_users()
            sent  = 0
            for u in users:
                if not u["is_agent"]:
                    continue
                try:
                    _bc_send(u["user_id"])
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} نماینده ارسال شد.", reply_markup=kb_admin_panel())
            from ..group_manager import send_to_topic as _stt
            _stt("broadcast_report", f"📢 <b>اطلاع‌رسانی (نمایندگان)</b>\n\n✅ برای {sent} نماینده ارسال شد.")
            return

        if sn == "admin_broadcast_admins" and is_admin(uid):
            from ..db import get_all_admin_users as _get_admins
            sent  = 0
            # ADMIN_IDS
            for aid in ADMIN_IDS:
                try:
                    _bc_send(aid)
                    sent += 1
                except Exception:
                    pass
            # Sub-admins
            for _ar in _get_admins():
                if _ar["user_id"] in ADMIN_IDS:
                    continue
                try:
                    _bc_send(_ar["user_id"])
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} ادمین ارسال شد.", reply_markup=kb_admin_panel())
            from ..group_manager import send_to_topic as _stt
            _stt("broadcast_report", f"📢 <b>اطلاع‌رسانی (ادمین‌ها)</b>\n\n✅ برای {sent} ادمین ارسال شد.")
            return

        # ── Wallet amount ──────────────────────────────────────────────────────
        if sn == "await_wallet_amount":
            amount = parse_int(message.text or "")
            if not amount or amount <= 0:
                bot.send_message(uid, "⚠️ لطفاً مبلغ معتبر وارد کنید.", reply_markup=back_button("main"))
                return
            state_set(uid, "wallet_charge_method", amount=amount)
            kb = types.InlineKeyboardMarkup()
            if is_gateway_available("card", uid, amount) and is_card_info_complete():
                kb.add(types.InlineKeyboardButton(setting_get("gw_card_display_name", "").strip() or "💳 کارت به کارت", callback_data="wallet:charge:card"))
            if is_gateway_available("crypto", uid, amount):
                kb.add(types.InlineKeyboardButton(setting_get("gw_crypto_display_name", "").strip() or "💎 ارز دیجیتال", callback_data="wallet:charge:crypto"))
            if is_gateway_available("tetrapay", uid, amount):
                kb.add(types.InlineKeyboardButton(setting_get("gw_tetrapay_display_name", "").strip() or "💳 درگاه کارت به کارت (TetraPay)", callback_data="wallet:charge:tetrapay"))
            if is_gateway_available("swapwallet_crypto", uid, amount):
                kb.add(types.InlineKeyboardButton(setting_get("gw_swapwallet_crypto_display_name", "").strip() or "💳 درگاه کارت به کارت و ارز دیجیتال (SwapWallet)", callback_data="wallet:charge:swapwallet_crypto"))
            if is_gateway_available("tronpays_rial", uid, amount):
                kb.add(types.InlineKeyboardButton(setting_get("gw_tronpays_rial_display_name", "").strip() or "💳 درگاه کارت به کارت (TronsPay)", callback_data="wallet:charge:tronpays_rial"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت",            callback_data="nav:main"))
            bot.send_message(
                uid,
                f"💰 مبلغ <b>{fmt_price(amount)}</b> تومان ثبت شد.\nروش پرداخت را انتخاب کنید:",
                reply_markup=kb
            )
            return

        # ── Wallet receipt ─────────────────────────────────────────────────────
        if sn == "await_wallet_receipt":
            payment_id  = sd.get("payment_id")
            file_id     = None
            text_value  = message.text or ""
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
            update_payment_receipt(payment_id, file_id, text_value.strip())
            state_clear(uid)
            bot.send_message(uid, "✅ رسید شما ارسال شد. لطفاً تا تأیید ادمین صبر کنید.",
                             reply_markup=kb_main(uid))
            send_payment_to_admins(payment_id)
            return

        # ── Purchase receipt ───────────────────────────────────────────────────
        if sn == "await_purchase_receipt":
            payment_id  = sd.get("payment_id")
            file_id     = None
            text_value  = message.text or ""
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
            update_payment_receipt(payment_id, file_id, text_value.strip())
            state_clear(uid)
            bot.send_message(uid, "✅ رسید شما ارسال شد. لطفاً تا تأیید ادمین صبر کنید.",
                             reply_markup=kb_main(uid))
            send_payment_to_admins(payment_id)
            return

        # ── Renewal receipt ────────────────────────────────────────────────────
        if sn == "await_renewal_receipt":
            payment_id  = sd.get("payment_id")
            file_id     = None
            text_value  = message.text or ""
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
            update_payment_receipt(payment_id, file_id, text_value.strip())
            state_clear(uid)
            bot.send_message(uid, "✅ رسید تمدید شما ارسال شد. لطفاً تا تأیید ادمین صبر کنید.",
                             reply_markup=kb_main(uid))
            send_payment_to_admins(payment_id)
            return

        # ── Admin: Type add/edit ───────────────────────────────────────────────
        if sn == "admin_add_type" and is_admin(uid):
            name = (message.text or "").strip()
            if not name:
                bot.send_message(uid, "⚠️ نام نوع نمی‌تواند خالی باشد.", reply_markup=back_button("admin:types"))
                return
            state_set(uid, "admin_add_type_desc", type_name=name)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ توضیحاتی نمی‌خواهم وارد کنم", callback_data="admin:type:skipdesc"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:types"))
            bot.send_message(uid,
                f"📝 توضیحات نوع <b>{esc(name)}</b> را وارد کنید:\n\n"
                "این توضیحات پس از ارسال کانفیگ به کاربر نمایش داده می‌شود.\n"
                "اگر نمی‌خواهید توضیحاتی وارد کنید، دکمه زیر را بزنید:", reply_markup=kb)
            return

        if sn == "admin_add_type_desc" and is_admin(uid):
            desc = (message.text or "").strip()
            name = sd["type_name"]
            try:
                add_type(name, desc)
                state_clear(uid)
                bot.send_message(uid, "✅ نوع جدید ثبت شد.")
                _show_admin_types(message)
            except sqlite3.IntegrityError:
                state_clear(uid)
                bot.send_message(uid, "⚠️ این نوع قبلاً ثبت شده است.", reply_markup=back_button("admin:types"))
            return

        if sn == "admin_edit_type" and is_admin(uid):
            new_name = (message.text or "").strip()
            if not new_name:
                bot.send_message(uid, "⚠️ نام معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            update_type(sd["type_id"], new_name)
            state_clear(uid)
            bot.send_message(uid, "✅ نوع با موفقیت ویرایش شد.")
            _show_admin_types(message)
            return

        if sn == "admin_edit_type_desc" and is_admin(uid):
            desc = (message.text or "").strip()
            update_type_description(sd["type_id"], desc)
            state_clear(uid)
            bot.send_message(uid, "✅ توضیحات با موفقیت ویرایش شد.")
            _show_admin_types(message)
            return

        # ── Admin: Package add ─────────────────────────────────────────────────
        if sn == "admin_add_package_name" and is_admin(uid):
            name = (message.text or "").strip()
            if not name:
                bot.send_message(uid, "⚠️ نام پکیج معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            state_set(uid, "admin_add_package_volume", type_id=sd["type_id"], package_name=name)
            bot.send_message(uid,
                "🔋 حجم پکیج را به گیگ وارد کنید:\n"
                "💡 برای حجم نامحدود عدد <b>0</b> بفرستید.\n"
                "💡 برای کمتر از ۱ گیگ اعشار وارد کنید (مثلاً <b>0.5</b>).",
                reply_markup=back_button("admin:types"))
            return

        if sn == "admin_add_package_volume" and is_admin(uid):
            volume = parse_volume(message.text or "")
            if volume is None:
                bot.send_message(uid, "⚠️ حجم معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            vol_label = "حجم نامحدود" if volume == 0 else fmt_vol(volume)
            state_set(uid, "admin_add_package_duration",
                      type_id=sd["type_id"], package_name=sd["package_name"], volume=volume)
            bot.send_message(uid,
                f"✅ حجم: <b>{vol_label}</b>\n\n"
                "⏰ مدت پکیج را به روز وارد کنید:\n"
                "💡 برای بدون محدودیت زمانی عدد <b>0</b> بفرستید.",
                reply_markup=back_button("admin:types"))
            return

        if sn == "admin_add_package_duration" and is_admin(uid):
            duration = parse_int(message.text or "")
            if duration is None or duration < 0:
                bot.send_message(uid, "⚠️ مدت معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            dur_label = "زمان نامحدود" if duration == 0 else f"{duration} روز"
            state_set(uid, "admin_add_package_price",
                      type_id=sd["type_id"], package_name=sd["package_name"],
                      volume=sd["volume"], duration=duration)
            bot.send_message(uid,
                f"✅ مدت: <b>{dur_label}</b>\n\n"
                "💰 قیمت پکیج را به تومان وارد کنید.\nبرای تست رایگان عدد <b>0</b> بفرستید:",
                reply_markup=back_button("admin:types"))
            return

        if sn == "admin_add_package_price" and is_admin(uid):
            price = parse_int(message.text or "")
            if price is None or price < 0:
                bot.send_message(uid, "⚠️ قیمت معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            add_package(sd["type_id"], sd["package_name"], sd["volume"], sd["duration"], price)
            state_clear(uid)
            vol_label = "حجم نامحدود" if sd["volume"] == 0 else fmt_vol(sd["volume"])
            dur_label = "زمان نامحدود" if sd["duration"] == 0 else f"{sd['duration']} روز"
            pri_label = "رایگان" if price == 0 else f"{fmt_price(price)} تومان"
            bot.send_message(uid,
                f"✅ پکیج با موفقیت ثبت شد.\n\n"
                f"📦 <b>{esc(sd['package_name'])}</b>\n"
                f"🔋 حجم: {vol_label}\n"
                f"⏰ مدت: {dur_label}\n"
                f"💰 قیمت: {pri_label}")
            _show_admin_types(message)
            return

        # ── Admin: Package edit field ──────────────────────────────────────────
        if sn == "admin_edit_pkg_field" and is_admin(uid):
            field_key  = sd["field_key"]
            package_id = sd["package_id"]
            db_field_map = {"name": "name", "price": "price", "volume": "volume_gb", "dur": "duration_days", "position": "position"}
            db_field   = db_field_map.get(field_key)
            raw        = (message.text or "").strip()
            if field_key == "name":
                if not raw:
                    bot.send_message(uid, "⚠️ نام معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                    return
                update_package_field(package_id, db_field, raw)
            elif field_key == "volume":
                val = parse_volume(raw)
                if val is None:
                    bot.send_message(uid, "⚠️ حجم معتبر وارد کنید (مثلاً <b>0.5</b> یا <b>10</b>).", reply_markup=back_button("admin:types"))
                    return
                update_package_field(package_id, db_field, val)
            else:
                val = parse_int(raw)
                if val is None or (field_key != "position" and val < 0) or (field_key == "position" and val < 1):
                    bot.send_message(uid, "⚠️ مقدار عددی معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                    return
                update_package_field(package_id, db_field, val)
            state_clear(uid)
            package_row = get_package(package_id)
            if package_row:
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✏️ ویرایش نام",   callback_data=f"admin:pkg:ef:name:{package_id}"))
                kb.add(types.InlineKeyboardButton("💰 ویرایش قیمت",  callback_data=f"admin:pkg:ef:price:{package_id}"))
                kb.add(types.InlineKeyboardButton("🔋 ویرایش حجم",   callback_data=f"admin:pkg:ef:volume:{package_id}"))
                kb.add(types.InlineKeyboardButton("⏰ ویرایش مدت",   callback_data=f"admin:pkg:ef:dur:{package_id}"))
                kb.add(types.InlineKeyboardButton("🔢 جایگاه نمایش", callback_data=f"admin:pkg:ef:position:{package_id}"))
                kb.add(types.InlineKeyboardButton("🔙 بازگشت",       callback_data="admin:types"))
                cur_pos = package_row["position"] if "position" in package_row.keys() else 0
                text = (
                    f"✅ ویرایش انجام شد\n\n"
                    f"📦 <b>{esc(package_row['name'])}</b>\n"
                    f"قیمت: {fmt_price(package_row['price'])} تومان\n"
                    f"حجم: {fmt_vol(package_row['volume_gb'])}\n"
                    f"مدت: {fmt_dur(package_row['duration_days'])}\n"
                    f"جایگاه: {cur_pos}"
                )
                send_or_edit(message, text, kb)
            else:
                bot.send_message(uid, "✅ پکیج با موفقیت ویرایش شد.")
                _show_admin_types(message)
            return

        # ── Admin: Config edit (inline) ────────────────────────────────────────
        if sn == "admin_cfg_edit_svc" and is_admin(uid):
            val = (message.text or "").strip()
            if not val:
                bot.send_message(uid, "⚠️ نام نمی‌تواند خالی باشد.", reply_markup=back_button(f"adm:stk:edt:{sd['config_id']}"))
                return
            update_config_field(sd["config_id"], "service_name", urllib.parse.quote(val))
            state_clear(uid)
            bot.send_message(uid, f"✅ نام سرویس تغییر کرد:\n<b>{esc(val)}</b>",
                             reply_markup=back_button(f"adm:stk:cfg:{sd['config_id']}"))
            return

        if sn == "admin_cfg_edit_text" and is_admin(uid):
            val = (message.text or "").strip()
            if not val:
                bot.send_message(uid, "⚠️ متن کانفیگ نمی‌تواند خالی باشد.", reply_markup=back_button(f"adm:stk:edt:{sd['config_id']}"))
                return
            update_config_field(sd["config_id"], "config_text", val)
            state_clear(uid)
            bot.send_message(uid, "✅ متن کانفیگ بروزرسانی شد.",
                             reply_markup=back_button(f"adm:stk:cfg:{sd['config_id']}"))
            return

        if sn == "admin_cfg_edit_inq" and is_admin(uid):
            val = (message.text or "").strip()
            update_config_field(sd["config_id"], "inquiry_link", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ لینک استعلام بروزرسانی شد.",
                             reply_markup=back_button(f"adm:stk:cfg:{sd['config_id']}"))
            return

        # ── Admin: Config add ──────────────────────────────────────────────────
        if sn == "admin_add_config_service" and is_admin(uid):
            service_name = (message.text or "").strip()
            if not service_name:
                bot.send_message(uid, "⚠️ نام سرویس را وارد کنید.", reply_markup=back_button("admin:add_config"))
                return
            state_set(uid, "admin_add_config_text",
                      package_id=sd["package_id"], type_id=sd["type_id"], service_name=service_name)
            bot.send_message(uid, "💝 متن کانفیگ را ارسال کنید:", reply_markup=back_button("admin:add_config"))
            return

        if sn == "admin_add_config_text" and is_admin(uid):
            config_text = (message.text or "").strip()
            if not config_text:
                bot.send_message(uid, "⚠️ متن کانفیگ را وارد کنید.", reply_markup=back_button("admin:add_config"))
                return
            state_set(uid, "admin_add_config_link",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      service_name=sd["service_name"], config_text=config_text)
            bot.send_message(uid, "🔗 لینک استعلام را ارسال کنید.\nاگر ندارید، <code>-</code> بفرستید.",
                             reply_markup=back_button("admin:add_config"))
            return

        if sn == "admin_add_config_link" and is_admin(uid):
            inquiry_link = (message.text or "").strip()
            if inquiry_link == "-":
                inquiry_link = ""
            add_config(sd["type_id"], sd["package_id"], sd["service_name"], sd["config_text"], inquiry_link)
            state_clear(uid)
            bot.send_message(uid, "✅ کانفیگ با موفقیت ثبت شد.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_add_config_bulk" and is_admin(uid):
            # Legacy fallback — should not reach here with new flow
            state_clear(uid)
            bot.send_message(uid, "⚠️ لطفاً دوباره از منو اقدام کنید.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_bulk_prefix" and is_admin(uid):
            prefix = (message.text or "").strip()
            pkg_id = sd["package_id"]
            state_set(uid, "admin_bulk_suffix",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      has_inquiry=sd["has_inquiry"], prefix=prefix)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ بعدی (بدون پسوند)", callback_data=f"adm:cfg:bulk:skipsuf:{pkg_id}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
            bot.send_message(uid,
                "✂️ <b>پسوند حذفی از نام کانفیگ</b>\n\n"
                "وقتی چندتا <b>اکسترنال پروکسی</b> ست می‌کنید، انتهای نام کانفیگ متن‌های اضافه اکسترنال‌ها اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن‌ها در نام کانفیگ بیاید، پسوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>-main</code>",
                reply_markup=kb)
            return

        if sn == "admin_bulk_suffix" and is_admin(uid):
            suffix = (message.text or "").strip()
            has_inq = sd.get("has_inquiry", False)
            prefix = sd.get("prefix", "")
            state_set(uid, "admin_bulk_data",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      has_inquiry=has_inq, prefix=prefix, suffix=suffix)
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
            bot.send_message(uid, fmt_text, reply_markup=back_button("admin:add_config"))
            return

        if sn == "admin_bulk_data" and is_admin(uid):
            # ── Extract raw text from message or TXT file ──
            raw = ""
            if message.document:
                # User sent a file — only accept .txt
                doc = message.document
                fname = (doc.file_name or "").lower()
                if not fname.endswith(".txt"):
                    bot.send_message(uid, "⚠️ فقط فایل با فرمت <b>.txt</b> پشتیبانی می‌شود.", parse_mode="HTML",
                                     reply_markup=back_button("admin:add_config"))
                    return
                try:
                    file_info = bot.get_file(doc.file_id)
                    downloaded = bot.download_file(file_info.file_path)
                    raw = downloaded.decode("utf-8", errors="ignore").strip()
                except Exception:
                    bot.send_message(uid, "⚠️ خطا در دانلود فایل. لطفاً دوباره ارسال کنید.",
                                     reply_markup=back_button("admin:add_config"))
                    return
            else:
                raw = (message.text or "").strip()

            if not raw:
                bot.send_message(uid, "⚠️ متنی ارسال نشده.", reply_markup=back_button("admin:add_config"))
                return
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            has_inq = sd.get("has_inquiry", False)
            prefix = sd.get("prefix", "")
            suffix = sd.get("suffix", "")
            type_id = sd["type_id"]
            package_id = sd["package_id"]

            configs = []
            if has_inq:
                # Pair lines: config, inquiry, config, inquiry...
                i = 0
                while i < len(lines):
                    cfg_line = lines[i]
                    inq_line = lines[i + 1] if i + 1 < len(lines) and lines[i + 1].lower().startswith("http") else ""
                    configs.append((cfg_line, inq_line))
                    i += 2 if inq_line else 1
            else:
                for line in lines:
                    configs.append((line, ""))

            success_count = 0
            success_names = []
            errors = []
            for idx, (cfg_text, inq_link) in enumerate(configs, 1):
                # Extract name from after #
                if "#" in cfg_text:
                    raw_name = cfg_text.rsplit("#", 1)[1]
                else:
                    raw_name = f"config-{idx}"
                # URL-decode the name
                try:
                    svc_name = urllib.parse.unquote(raw_name)
                except Exception:
                    svc_name = raw_name
                # Strip prefix
                if prefix and svc_name.startswith(prefix):
                    svc_name = svc_name[len(prefix):]
                # Also try URL-decoded prefix
                if prefix:
                    try:
                        decoded_prefix = urllib.parse.unquote(prefix)
                        if decoded_prefix != prefix and svc_name.startswith(decoded_prefix):
                            svc_name = svc_name[len(decoded_prefix):]
                    except Exception:
                        pass
                # Strip suffix
                if suffix and svc_name.endswith(suffix):
                    svc_name = svc_name[:-len(suffix)]
                if suffix:
                    try:
                        decoded_suffix = urllib.parse.unquote(suffix)
                        if decoded_suffix != suffix and svc_name.endswith(decoded_suffix):
                            svc_name = svc_name[:-len(decoded_suffix)]
                    except Exception:
                        pass
                svc_name = svc_name.strip().strip("-").strip("_").strip()
                if not svc_name:
                    svc_name = f"config-{idx}"
                if not cfg_text:
                    errors.append(f"کانفیگ {idx}: متن خالی")
                    continue
                try:
                    add_config(type_id, package_id, svc_name, cfg_text, inq_link)
                    success_count += 1
                    success_names.append(svc_name)
                except Exception as e:
                    errors.append(f"کانفیگ {idx}: {str(e)}")

            # Auto-fulfill any waiting pending orders for this package
            auto_fulfilled = 0
            auto_fulfill_err = ""
            if success_count > 0:
                try:
                    auto_fulfilled = auto_fulfill_pending_orders(package_id)
                except Exception as e:
                    auto_fulfill_err = str(e)

            state_clear(uid)
            result = f"✅ <b>{success_count}</b> کانفیگ با موفقیت ثبت شد."
            if success_names:
                names_text = "\n".join(f"  • {esc(n)}" for n in success_names)
                result += f"\n\n📝 <b>نام کانفیگ‌های ثبت‌شده:</b>\n{names_text}"
            if auto_fulfilled > 0:
                result += f"\n\n🚀 <b>{auto_fulfilled}</b> سفارش در انتظار به صورت خودکار تحویل داده شد."
            if auto_fulfill_err:
                result += f"\n\n⚠️ خطا در تحویل سفارش‌های در انتظار:\n<code>{esc(auto_fulfill_err)}</code>"
            if errors:
                result += "\n\n❌ خطاها:\n" + "\n".join(errors[:20])
            bot.send_message(uid, result, reply_markup=kb_admin_panel())
            return

        # ── Admin: Settings ────────────────────────────────────────────────────
        if sn == "admin_set_support" and is_admin(uid):
            setting_set("support_username", (message.text or "").strip())
            state_clear(uid)
            bot.send_message(uid, "✅ آیدی پشتیبانی ذخیره شد.", reply_markup=back_button("adm:set:support"))
            return

        if sn == "admin_set_support_link" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("support_link", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ لینک پشتیبانی ذخیره شد.", reply_markup=back_button("adm:set:support"))
            return

        if sn == "admin_set_support_desc" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("support_link_desc", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ توضیحات پشتیبانی ذخیره شد.", reply_markup=back_button("adm:set:support"))
            return

        # ── Referral settings inputs ───────────────────────────────────────────
        if sn == "admin_ref_banner" and is_admin(uid):
            if message.photo:
                photo_id = message.photo[-1].file_id
                caption = (message.caption or "").strip()
                setting_set("referral_banner_photo", photo_id)
                setting_set("referral_banner_text", caption)
            else:
                setting_set("referral_banner_text", (message.text or "").strip())
                setting_set("referral_banner_photo", "")
            state_clear(uid)
            bot.send_message(uid, "✅ بنر اشتراک‌گذاری ذخیره شد.", reply_markup=back_button("adm:ref:settings"))
            return

        if sn == "admin_ref_sr_count" and is_admin(uid):
            count = parse_int(message.text or "")
            if not count or count <= 0:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید.", reply_markup=back_button("adm:ref:settings"))
                return
            setting_set("referral_start_reward_count", str(count))
            state_clear(uid)
            bot.send_message(uid, f"✅ تعداد زیرمجموعه برای هدیه استارت: {count}", reply_markup=back_button("adm:ref:settings"))
            return

        if sn == "admin_ref_sr_amount" and is_admin(uid):
            amount = parse_int(message.text or "")
            if amount is None or amount < 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("adm:ref:settings"))
                return
            setting_set("referral_start_reward_amount", str(amount))
            state_clear(uid)
            bot.send_message(uid, f"✅ مبلغ هدیه استارت: {fmt_price(amount)} تومان", reply_markup=back_button("adm:ref:settings"))
            return

        if sn == "admin_ref_pr_count" and is_admin(uid):
            count = parse_int(message.text or "")
            if not count or count <= 0:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید.", reply_markup=back_button("adm:ref:settings"))
                return
            setting_set("referral_purchase_reward_count", str(count))
            state_clear(uid)
            bot.send_message(uid, f"✅ تعداد خرید برای هدیه: {count}", reply_markup=back_button("adm:ref:settings"))
            return

        if sn == "admin_ref_pr_amount" and is_admin(uid):
            amount = parse_int(message.text or "")
            if amount is None or amount < 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("adm:ref:settings"))
                return
            setting_set("referral_purchase_reward_amount", str(amount))
            state_clear(uid)
            bot.send_message(uid, f"✅ مبلغ هدیه خرید: {fmt_price(amount)} تومان", reply_markup=back_button("adm:ref:settings"))
            return

        if sn == "admin_set_card" and is_admin(uid):
            setting_set("payment_card", normalize_text_number(message.text or ""))
            state_clear(uid)
            bot.send_message(uid, "✅ شماره کارت ذخیره شد.", reply_markup=back_button("adm:set:gw:card"))
            return

        if sn == "admin_set_bank" and is_admin(uid):
            setting_set("payment_bank", (message.text or "").strip())
            state_clear(uid)
            bot.send_message(uid, "✅ نام بانک ذخیره شد.", reply_markup=back_button("adm:set:gw:card"))
            return

        if sn == "admin_set_owner" and is_admin(uid):
            setting_set("payment_owner", (message.text or "").strip())
            state_clear(uid)
            bot.send_message(uid, "✅ نام صاحب کارت ذخیره شد.", reply_markup=back_button("adm:set:gw:card"))
            return

        if sn == "admin_set_crypto_wallet" and is_admin(uid):
            coin_key = sd["coin_key"]
            val      = (message.text or "").strip()
            setting_set(f"crypto_{coin_key}", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ آدرس ولت ذخیره شد.", reply_markup=back_button("adm:set:gw:crypto"))
            return

        if sn == "admin_set_tetrapay_key" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("tetrapay_api_key", val)
            state_clear(uid)
            bot.send_message(uid, "✅ کلید API تتراپی ذخیره شد.", reply_markup=back_button("adm:set:gw:tetrapay"))
            return

        if sn == "admin_set_swapwallet_crypto_key" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("swapwallet_crypto_api_key", val)
            state_clear(uid)
            bot.send_message(uid, "✅ کلید API سواپ ولت (کریپتو) ذخیره شد.", reply_markup=back_button("adm:set:gw:swapwallet_crypto"))
            return

        if sn == "admin_set_swapwallet_crypto_username" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("swapwallet_crypto_username", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ نام کاربری فروشگاه سواپ ولت (کریپتو) ذخیره شد.", reply_markup=back_button("adm:set:gw:swapwallet_crypto"))
            return

        if sn == "admin_set_gw_display_name" and is_admin(uid):
            gw = sd.get("gw", "")
            val = (message.text or "").strip()
            setting_set(f"gw_{gw}_display_name", "" if val == "-" else val)
            state_clear(uid)
            msg = "✅ نام نمایشی درگاه ذخیره شد." if val != "-" else "✅ نام نمایشی به پیش‌فرض بازگشت داده شد."
            bot.send_message(uid, msg, reply_markup=back_button(f"adm:set:gw:{gw}"))
            return

        if sn == "admin_set_tronpays_rial_key" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("tronpays_rial_api_key", val)
            state_clear(uid)
            bot.send_message(uid, "✅ کلید API TronPays ذخیره شد.", reply_markup=back_button("adm:set:gw:tronpays_rial"))
            return

        if sn == "admin_set_tronpays_rial_cb_url" and is_admin(uid):
            val = (message.text or "").strip()
            if val and not (val.startswith("http://") or val.startswith("https://")):
                bot.send_message(uid, "⚠️ URL باید با <code>https://</code> یا <code>http://</code> شروع شود:", reply_markup=back_button("adm:set:gw:tronpays_rial"))
                return
            setting_set("tronpays_rial_callback_url", val)
            state_clear(uid)
            bot.send_message(uid, f"✅ Callback URL ذخیره شد:\n<code>{val or 'https://example.com/'}</code>", reply_markup=back_button("adm:set:gw:tronpays_rial"))
            return

        if sn == "admin_gw_range_min" and is_admin(uid):
            gw = sd.get("gw", "")
            val = (message.text or "").strip()
            if val in ("0", "-", "بدون حداقل"):
                setting_set(f"gw_{gw}_range_min", "")
            elif val.isdigit():
                setting_set(f"gw_{gw}_range_min", val)
            else:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید یا <code>0</code> برای بدون حداقل:", reply_markup=back_button(f"adm:gw:{gw}:range"))
                return
            state_set(uid, "admin_gw_range_max", gw=gw)
            bot.send_message(uid,
                "📊 <b>حداکثر مبلغ</b> (تومان) را وارد کنید.\n\n"
                "برای <b>بدون حداکثر</b>، عدد <code>0</code> یا <code>-</code> ارسال کنید:")
            return

        if sn == "admin_gw_range_max" and is_admin(uid):
            gw = sd.get("gw", "")
            val = (message.text or "").strip()
            if val in ("0", "-", "بدون حداکثر"):
                setting_set(f"gw_{gw}_range_max", "")
            elif val.isdigit():
                setting_set(f"gw_{gw}_range_max", val)
            else:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید یا <code>0</code> برای بدون حداکثر:", reply_markup=back_button(f"adm:gw:{gw}:range"))
                return
            state_clear(uid)
            bot.send_message(uid, "✅ بازه پرداختی ذخیره شد.", reply_markup=back_button(f"adm:gw:{gw}:range"))
            return

        if sn == "admin_set_channel" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("channel_id", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ کانال ذخیره شد.", reply_markup=back_button("admin:settings"))
            return

        if sn == "admin_set_start_text" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("start_text", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ متن استارت ذخیره شد.", reply_markup=back_button("admin:settings"))
            return

        # ── Admin: Free Test settings ──────────────────────────────────────────
        if sn == "admin_set_agent_test_limit" and is_admin(uid):
            val = (message.text or "").strip()
            if val == "0":
                setting_set("agent_test_limit", "0")
                state_clear(uid)
                bot.send_message(uid, "✅ محدودیت تست همکاران غیرفعال شد.", reply_markup=back_button("adm:set:freetest"))
                return
            parts = val.split()
            if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("day", "week", "month"):
                bot.send_message(uid,
                    "⚠️ فرمت نادرست. مثال: <code>5 day</code> یا <code>10 week</code> یا <code>20 month</code>\nبرای غیرفعال: <code>0</code>",
                    reply_markup=back_button("adm:set:freetest"))
                return
            setting_set("agent_test_limit", parts[0])
            setting_set("agent_test_period", parts[1])
            state_clear(uid)
            period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
            bot.send_message(uid,
                f"✅ تست همکاران: {parts[0]} عدد در {period_labels[parts[1]]}",
                reply_markup=back_button("adm:set:freetest"))
            return

        # ── Admin: Backup settings ─────────────────────────────────────────────
        if sn == "admin_set_backup_interval" and is_admin(uid):
            val = parse_int(message.text or "")
            if not val or val < 1:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید.", reply_markup=back_button("admin:backup"))
                return
            setting_set("backup_interval", str(val))
            state_clear(uid)
            bot.send_message(uid, f"✅ بازه بکاپ به {val} ساعت تنظیم شد.", reply_markup=back_button("admin:backup"))
            return

        if sn == "admin_set_backup_target" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("backup_target_id", val)
            state_clear(uid)
            bot.send_message(uid, "✅ مقصد بکاپ ذخیره شد.", reply_markup=back_button("admin:backup"))
            return

        if sn == "admin_set_group_id" and is_admin(uid):
            from ..group_manager import ensure_group_topics
            val = (message.text or "").strip()
            if not val.lstrip("-").isdigit():
                bot.send_message(uid,
                    "⚠️ آیدی گروه باید عددی باشد.\nمثال: <code>-1001234567890</code>",
                    reply_markup=back_button("admin:group"))
                return
            setting_set("group_id", val)
            state_clear(uid)
            bot.send_message(uid,
                f"✅ آیدی گروه <code>{val}</code> ذخیره شد.\n\n"
                "در حال ساخت تاپیک‌ها...", parse_mode="HTML")
            result = ensure_group_topics()
            bot.send_message(uid, f"🛠 <b>نتیجه ساخت تاپیک:</b>\n\n{result}",
                             parse_mode="HTML", reply_markup=back_button("admin:group"))
            return

        if sn == "admin_restore_backup" and is_admin(uid):
            if not message.document:
                bot.send_message(uid, "⚠️ لطفاً فایل بکاپ (.db) را ارسال کنید.", reply_markup=back_button("admin:backup"))
                return
            file_name = message.document.file_name or ""
            if not file_name.lower().endswith(".db"):
                bot.send_message(uid, "⚠️ فقط فایل با پسوند <code>.db</code> قابل قبول است.", reply_markup=back_button("admin:backup"))
                return
            try:
                file_info = bot.get_file(message.document.file_id)
                downloaded = bot.download_file(file_info.file_path)
                # ابتدا بکاپ از دیتابیس فعلی
                import shutil
                backup_ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                pre_restore_backup = f"{DB_NAME}.pre_restore_{backup_ts}"
                if os.path.exists(DB_NAME):
                    shutil.copy2(DB_NAME, pre_restore_backup)
                # جایگزینی دیتابیس
                with open(DB_NAME, "wb") as f:
                    f.write(downloaded)
                state_clear(uid)
                bot.send_message(uid,
                    f"✅ بکاپ با موفقیت بازیابی شد.\n\n"
                    f"💾 نسخه قبلی در <code>{esc(pre_restore_backup)}</code> ذخیره شد.",
                    reply_markup=back_button("admin:backup"))
            except Exception as e:
                bot.send_message(uid, f"❌ خطا در بازیابی بکاپ: {esc(str(e))}", reply_markup=back_button("admin:backup"))
            return

        # ── Admin: User Search ────────────────────────────────────────────────
        if sn == "admin_user_search" and is_admin(uid):
            query_text = (message.text or "").strip()
            if not query_text:
                bot.send_message(uid, "⚠️ متن جستجو را ارسال کنید.")
                return
            state_clear(uid)
            rows = search_users(query_text)
            if not rows:
                bot.send_message(uid, "❌ کاربری یافت نشد.", reply_markup=back_button("admin:users"))
                return
            kb = types.InlineKeyboardMarkup()
            for row in rows:
                status_icon = "🔘" if row["status"] == "safe" else "⚠️"
                agent_icon  = "🤝 " if row["is_agent"] else ""
                uname       = f"@{row['username']}" if row["username"] else str(row["user_id"])
                name_part   = row["full_name"] or f"(آیدی: {row['user_id']})"
                buy_tag     = f" 🛍{row['purchase_count']}" if row["purchase_count"] else ""
                label = f"{status_icon} {agent_icon}{name_part} | {uname}{buy_tag}"
                kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:usr:v:{row['user_id']}"))
            kb.add(types.InlineKeyboardButton("🔍 جستجوی جدید", callback_data="adm:usr:search"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="admin:users"))
            bot.send_message(uid, f"🔍 <b>نتایج جستجو</b> — {len(rows)} کاربر یافت شد:", reply_markup=kb)
            return

        # ── Admin: Stock Search ────────────────────────────────────────────────
        if sn in ("admin_search_by_link", "admin_search_by_config", "admin_search_by_name") and is_admin(uid):
            query_text = (message.text or "").strip()
            if not query_text:
                bot.send_message(uid, "⚠️ متن جستجو را ارسال کنید.")
                return
            state_clear(uid)
            search_param = f"%{query_text}%"
            if sn == "admin_search_by_link":
                col_filter = "c.inquiry_link LIKE ?"
            elif sn == "admin_search_by_config":
                col_filter = "c.config_text LIKE ?"
            else:
                col_filter = "c.service_name LIKE ?"
            with get_conn() as conn:
                rows = conn.execute(
                    f"SELECT c.id, c.service_name, c.sold_to, c.is_expired FROM configs c WHERE {col_filter} ORDER BY c.id DESC LIMIT 50",
                    (search_param,)
                ).fetchall()
            if not rows:
                bot.send_message(uid, "❌ نتیجه‌ای یافت نشد.", reply_markup=back_button("adm:stk:search"))
                return
            kb = types.InlineKeyboardMarkup()
            for r in rows:
                label = urllib.parse.unquote(r["service_name"] or "") or f"#{r['id']}"
                if r["is_expired"]:
                    label = "⛔ " + label
                elif r["sold_to"]:
                    label = "✅ " + label
                else:
                    label = "📦 " + label
                kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:stk:cfg:{r['id']}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:stk:search"))
            bot.send_message(uid, f"🔍 نتایج جستجو ({len(rows)}):", reply_markup=kb)
            return

        # ── Admin: Balance edit ────────────────────────────────────────────────
        if sn in ("admin_bal_add", "admin_bal_sub") and is_admin(uid):
            amount        = parse_int(message.text or "")
            target_user_id = sd["target_user_id"]
            if not amount or amount <= 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("admin:users"))
                return
            delta = amount if sn == "admin_bal_add" else -amount
            update_balance(target_user_id, delta)
            state_clear(uid)
            action_label = "اضافه" if delta > 0 else "کاهش"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 بازگشت به کاربر", callback_data=f"adm:usr:v:{target_user_id}"))
            bot.send_message(uid, f"✅ موجودی {action_label} یافت.", reply_markup=kb)
            try:
                msg = f"{'➕' if delta > 0 else '➖'} موجودی شما توسط ادمین {action_label} یافت.\n💰 مبلغ: {fmt_price(abs(amount))} تومان"
                bot.send_message(target_user_id, msg)
            except Exception:
                pass
            return

        # ── Admin: Agency price (per-package, mode=package) ─────────────────
        if sn == "admin_set_agency_price" and is_admin(uid):
            target_user_id = sd["target_user_id"]
            package_id     = sd["package_id"]
            val            = parse_int(message.text or "")
            if val is None or val < 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("admin:users"))
                return
            if val == 0:
                with get_conn() as conn:
                    conn.execute("DELETE FROM agency_prices WHERE user_id=? AND package_id=?",
                                 (target_user_id, package_id))
                state_clear(uid)
                bot.send_message(uid, "✅ قیمت اختصاصی حذف شد (قیمت پیش‌فرض اعمال می‌شود).",
                                 reply_markup=kb_admin_panel())
            else:
                set_agency_price(target_user_id, package_id, val)
                state_clear(uid)
                bot.send_message(uid, f"✅ قیمت اختصاصی {fmt_price(val)} تومان ثبت شد.",
                                 reply_markup=kb_admin_panel())
            return

        # ── Admin: Agency global discount value ────────────────────────────────
        if sn == "admin_agcfg_global_val" and is_admin(uid):
            target_user_id = sd["target_user_id"]
            dtype          = sd.get("dtype", "pct")
            val            = parse_int(message.text or "")
            if val is None or val < 0:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید.")
                return
            if dtype == "pct" and val > 100:
                bot.send_message(uid, "⚠️ درصد بیشتر از 100 مجاز نیست.")
                return
            set_agency_price_config(target_user_id, "global",
                "pct" if dtype == "pct" else "toman", val)
            state_clear(uid)
            label = f"{val}%" if dtype == "pct" else f"{fmt_price(val)} تومان"
            bot.send_message(uid,
                f"✅ تخفیف کل محصولات: <b>{label}</b> تنظیم شد.",
                reply_markup=kb_admin_panel())
            return

        # ── Admin: Agency type discount value ──────────────────────────────────
        if sn == "admin_agcfg_type_val" and is_admin(uid):
            target_user_id = sd["target_user_id"]
            type_id        = sd.get("type_id")
            dtype          = sd.get("dtype", "pct")
            val            = parse_int(message.text or "")
            if val is None or val < 0:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید.")
                return
            if dtype == "pct" and val > 100:
                bot.send_message(uid, "⚠️ درصد بیشتر از 100 مجاز نیست.")
                return
            set_agency_type_discount(target_user_id, type_id,
                "pct" if dtype == "pct" else "toman", val)
            state_clear(uid)
            label = f"{val}%" if dtype == "pct" else f"{fmt_price(val)} تومان"
            bot.send_message(uid,
                f"✅ تخفیف دسته #{type_id}: <b>{label}</b> تنظیم شد.",
                reply_markup=kb_admin_panel())
            return

        # ── Admin: Default agency discount % ──────────────────────────────────
        if sn == "admin_set_default_discount_pct" and is_admin(uid):
            val = parse_int(message.text or "")
            if val is None or val < 0 or val > 100:
                bot.send_message(uid, "⚠️ عددی بین 0 تا 100 وارد کنید.")
                return
            setting_set("agency_default_discount_pct", str(val))
            state_clear(uid)
            bot.send_message(uid, f"✅ تخفیف پیش‌فرض نمایندگی به <b>{val}%</b> تغییر یافت.",
                             reply_markup=back_button("admin:settings"))
            return

        # ── Admin: Add agent (search) ─────────────────────────────────────────
        if sn == "admin_agent_add_search" and is_admin(uid):
            raw = (message.text or "").strip()
            target_user = None
            if raw.lstrip("-").isdigit():
                target_user = get_user(int(raw))
            if not target_user:
                results = search_users(raw)
                if results:
                    target_user = results[0]
            if not target_user:
                bot.send_message(uid, "⚠️ کاربری با این شناسه یافت نشد.",
                                 reply_markup=back_button("admin:agents"))
                return
            state_clear(uid)
            if target_user["is_agent"]:
                bot.send_message(uid,
                    f"ℹ️ کاربر <b>{esc(target_user['full_name'])}</b> قبلاً نماینده است.",
                    reply_markup=back_button("admin:agents"))
                return
            set_user_agent(target_user["user_id"], 1)
            default_pct = int(setting_get("agency_default_discount_pct", "20") or "20")
            if default_pct > 0:
                set_agency_price_config(target_user["user_id"], "global", "pct", default_pct)
            kb_r = types.InlineKeyboardMarkup()
            kb_r.add(types.InlineKeyboardButton(
                "💰 قیمت نمایندگی",
                callback_data=f"adm:agcfg:{target_user['user_id']}"
            ))
            kb_r.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:agents"))
            bot.send_message(uid,
                f"✅ کاربر <b>{esc(target_user['full_name'])}</b> (کد <code>{target_user['user_id']}</code>) به نماینده تبدیل شد.\n\n"
                f"📊 تخفیف پیش‌فرض {default_pct}% اعمال شد.",
                reply_markup=kb_r)
            try:
                bot.send_message(target_user["user_id"],
                    "🎉 <b>شما به عنوان نماینده تمام سیستم اضافه شدید!</b>")
            except Exception:
                pass
            return
            target_user_id = sd["target_user_id"]
            package_id     = sd["package_id"]
            val            = parse_int(message.text or "")
            if val is None or val < 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("admin:users"))
                return
            if val == 0:
                with get_conn() as conn:
                    conn.execute("DELETE FROM agency_prices WHERE user_id=? AND package_id=?",
                                 (target_user_id, package_id))
                state_clear(uid)
                bot.send_message(uid, "✅ قیمت اختصاصی حذف شد (قیمت پیش‌فرض اعمال می‌شود).",
                                 reply_markup=kb_admin_panel())
            else:
                set_agency_price(target_user_id, package_id, val)
                state_clear(uid)
                bot.send_message(uid, f"✅ قیمت اختصاصی {fmt_price(val)} تومان ثبت شد.",
                                 reply_markup=kb_admin_panel())
            return

        # ── Admin: Add admin — resolve user ID ────────────────────────────────
        if sn == "admin_mgr_await_id" and uid in ADMIN_IDS:
            raw = (message.text or "").strip()
            target_id = None
            # Try numeric ID first
            if raw.lstrip("-").isdigit():
                target_id = int(raw)
            else:
                # Try username lookup (remove leading @)
                uname = raw.lstrip("@").lower()
                with get_conn() as conn:
                    row_u = conn.execute(
                        "SELECT user_id FROM users WHERE LOWER(username)=? LIMIT 1",
                        (uname,)
                    ).fetchone()
                if row_u:
                    target_id = row_u["user_id"]
            if not target_id:
                bot.send_message(uid,
                    "⚠️ کاربر یافت نشد. آیدی عددی یا یوزرنیم را دقیق وارد کنید.",
                    reply_markup=back_button("admin:admins"))
                return
            if target_id in ADMIN_IDS:
                bot.send_message(uid,
                    "⚠️ این کاربر اونر است و نیاز به ثبت ادمین ندارد.",
                    reply_markup=back_button("admin:admins"))
                state_clear(uid)
                return
            state_set(uid, "admin_mgr_select_perms", target_user_id=target_id, perms="{}")
            _show_perm_selection(message, uid, target_id, {}, edit_mode=False)
            return

        # ── Admin: Payment approval ────────────────────────────────────────────
        if sn == "admin_payment_approve_note" and is_admin(uid):
            payment_id = sd["payment_id"]
            raw_note   = (message.text or "").strip()
            note = "واریزی شما تأیید شد." if (not raw_note or raw_note == "➖") else raw_note
            finish_card_payment_approval(payment_id, note, approved=True)
            state_clear(uid)
            bot.send_message(uid, "✅ درخواست با موفقیت تأیید شد.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_payment_reject_note" and is_admin(uid):
            payment_id = sd["payment_id"]
            raw_note   = (message.text or "").strip()
            note = "رسید شما رد شد." if (not raw_note or raw_note == "➖") else raw_note
            finish_card_payment_approval(payment_id, note, approved=False)
            state_clear(uid)
            bot.send_message(uid, "✅ درخواست با موفقیت رد شد.", reply_markup=kb_admin_panel())
            return

        # ── Admin: Pending order config entry ─────────────────────────────────
        if sn == "admin_pending_cfg_name" and is_admin(uid):
            cfg_name = (message.text or "").strip()
            if not cfg_name:
                bot.send_message(uid, "⚠️ نام سرویس نمی‌تواند خالی باشد. لطفاً دوباره ارسال کنید:")
                return
            state_set(uid, "admin_pending_cfg_text", pending_id=sd["pending_id"], cfg_name=cfg_name)
            bot.send_message(uid, "✅ نام ثبت شد.\n\nحالا <b>متن کانفیگ</b> را ارسال کنید:")
            return

        if sn == "admin_pending_cfg_text" and is_admin(uid):
            cfg_text = (message.text or "").strip()
            if not cfg_text:
                bot.send_message(uid, "⚠️ متن کانفیگ نمی‌تواند خالی باشد. لطفاً دوباره ارسال کنید:")
                return
            state_set(uid, "admin_pending_cfg_link",
                      pending_id=sd["pending_id"], cfg_name=sd["cfg_name"], cfg_text=cfg_text)
            bot.send_message(uid,
                "✅ کانفیگ ثبت شد.\n\n"
                "اگر <b>لینک استعلام</b> دارد ارسال کنید، در غیر اینصورت <b>ندارد</b> بنویسید:")
            return

        if sn == "admin_pending_cfg_link" and is_admin(uid):
            raw_link = (message.text or "").strip()
            inquiry_link = None if raw_link.lower() in ("ندارد", "no", "-", "") else raw_link
            pending_id = sd["pending_id"]
            cfg_name   = sd["cfg_name"]
            cfg_text   = sd["cfg_text"]
            state_clear(uid)
            # Deliver config to the user
            ok = _complete_pending_order(pending_id, cfg_name, cfg_text, inquiry_link)
            if ok:
                bot.send_message(uid, "✅ کانفیگ برای کاربر ارسال شد.", reply_markup=kb_admin_panel())
            else:
                bot.send_message(uid, "⚠️ خطا در تکمیل سفارش. ممکن است قبلاً تکمیل شده باشد.",
                                 reply_markup=kb_admin_panel())
            return

        # ── Agency request text ────────────────────────────────────────────────
        if sn == "agency_request_text":
            req_text = (message.text or "").strip() or "بدون متن"
            state_clear(uid)
            user = get_user(uid)
            bot.send_message(uid, "✅ درخواست نمایندگی شما ارسال شد.\n⏳ لطفاً منتظر بررسی ادمین باشید.",
                             reply_markup=kb_main(uid))
            text = (
                f"🤝 <b>درخواست نمایندگی جدید</b>\n\n"
                f"👤 نام: {esc(user['full_name'])}\n"
                f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
                f"🔢 آیدی: <code>{user['user_id']}</code>\n\n"
                f"📝 متن درخواست:\n{esc(req_text)}"
            )
            admin_kb = types.InlineKeyboardMarkup()
            admin_kb.row(
                types.InlineKeyboardButton("✅ تأیید", callback_data=f"agency:approve:{uid}"),
                types.InlineKeyboardButton("❌ رد", callback_data=f"agency:reject:{uid}"),
            )
            for admin_id in ADMIN_IDS:
                try:
                    bot.send_message(admin_id, text, reply_markup=admin_kb)
                except Exception:
                    pass
            return

        # ── Agency approval note ───────────────────────────────────────────────
        if sn == "agency_approve_note" and is_admin(uid):
            note = (message.text or "").strip()
            target_uid = sd["target_user_id"]
            state_clear(uid)
            with get_conn() as conn:
                conn.execute("UPDATE users SET is_agent=1 WHERE user_id=?", (target_uid,))
            default_pct = int(setting_get("agency_default_discount_pct", "20") or "20")
            if default_pct > 0:
                set_agency_price_config(target_uid, "global", "pct", default_pct)
            kb_conf = types.InlineKeyboardMarkup()
            kb_conf.add(types.InlineKeyboardButton(
                "💰 قیمت نمایندگی کاربر", callback_data=f"adm:agcfg:{target_uid}"))
            kb_conf.add(types.InlineKeyboardButton(
                "🔙 بازگشت", callback_data="admin:users"))
            bot.send_message(uid,
                f"✅ نمایندگی تأیید شد.\n📊 تخفیف پیش‌فرض {default_pct}% اعمال شد.",
                reply_markup=kb_conf)
            _show_admin_user_detail_msg(uid, target_uid)
            try:
                msg = "🎉 <b>درخواست نمایندگی شما تأیید شد!</b>\n\nاکنون شما نماینده هستید."
                if note:
                    msg += f"\n\n📝 پیام ادمین:\n{esc(note)}"
                bot.send_message(target_uid, msg)
            except Exception:
                pass
            return

        # ── Agency rejection reason ────────────────────────────────────────────
        if sn == "agency_reject_reason" and is_admin(uid):
            reason = (message.text or "").strip() or "بدون دلیل"
            target_uid = sd["target_user_id"]
            state_clear(uid)
            bot.send_message(uid, "✅ درخواست نمایندگی رد شد.", reply_markup=kb_admin_panel())
            try:
                bot.send_message(target_uid,
                    f"❌ <b>درخواست نمایندگی شما رد شد.</b>\n\n📝 دلیل:\n{esc(reason)}")
            except Exception:
                pass
            return

        # ── Admin: Edit rules text ─────────────────────────────────────────────
        if sn == "admin_edit_rules_text" and is_admin(uid):
            text_val = (message.text or "").strip()
            if not text_val:
                bot.send_message(uid, "⚠️ متن خالی مجاز نیست.", reply_markup=back_button("adm:set:rules"))
                return
            setting_set("purchase_rules_text", text_val)
            state_clear(uid)
            bot.send_message(uid, "✅ متن قوانین خرید ذخیره شد.", reply_markup=back_button("adm:set:rules"))
            return

        # ── Panel: Register Panel Config (multi-step) ──────────────────────────
        if sn == "panel_add_name" and is_admin(uid):
            name = (message.text or "").strip()
            if not name:
                bot.send_message(uid, "⚠️ Name cannot be empty."); return
            state_set(uid, "panel_add_ip", name=name)
            bot.send_message(uid, f"🖥 Step 2/5: Enter Panel <b>IP</b> (default 127.0.0.1):")
            return

        if sn == "panel_add_ip" and is_admin(uid):
            raw = (message.text or "").strip()
            ip  = raw if raw else "127.0.0.1"
            state_set(uid, "panel_add_port", name=sd["name"], ip=ip)
            bot.send_message(uid, "🔌 Step 3/5: Enter Panel <b>Port</b> (default 2053):")
            return

        if sn == "panel_add_port" and is_admin(uid):
            raw  = (message.text or "").strip()
            port = raw if raw.isdigit() else "2053"
            state_set(uid, "panel_add_patch", name=sd["name"], ip=sd["ip"], port=port)
            bot.send_message(uid, "📄 Step 4/5: Enter <b>Patch</b> (optional, press / or leave blank):")
            return

        if sn == "panel_add_patch" and is_admin(uid):
            raw   = (message.text or "").strip()
            patch = raw if raw else ""
            state_set(uid, "panel_add_user", name=sd["name"], ip=sd["ip"],
                      port=sd["port"], patch=patch)
            bot.send_message(uid, "👤 Step 5/6: Enter <b>Panel Username</b>:")
            return

        if sn == "panel_add_user" and is_admin(uid):
            username = (message.text or "").strip()
            if not username:
                bot.send_message(uid, "⚠️ Username cannot be empty."); return
            state_set(uid, "panel_add_pass", name=sd["name"], ip=sd["ip"],
                      port=sd["port"], patch=sd["patch"], username=username)
            bot.send_message(uid, "🔑 Step 6/6: Enter <b>Panel Password</b>:")
            return

        if sn == "panel_add_pass" and is_admin(uid):
            password = (message.text or "").strip()
            if not password:
                bot.send_message(uid, "⚠️ Password cannot be empty."); return
            state_clear(uid)
            new_id = add_panel(sd["name"], sd["ip"], int(sd["port"]), sd["patch"], sd["username"], password)
            bot.send_message(uid,
                f"✅ <b>Panel Registered!</b>\n\n"
                f"🖥 Name: {esc(sd['name'])}\n"
                f"🌐 IP: {esc(sd['ip'])}\n"
                f"🔌 Port: {sd['port']}\n"
                f"📄 Patch: {esc(sd['patch'] or '/')}\n"
                f"👤 Username: {esc(sd['username'])}\n"
                f"🆔 Panel ID: #{new_id}",
                reply_markup=kb_admin_panel(uid))
            return

        # ── Pinned Messages ───────────────────────────────────────────────────
        if sn == "admin_pin_add" and admin_has_perm(uid, "settings"):
            text = (message.text or "").strip()
            if not text:
                bot.send_message(uid, "⚠️ متن پیام نمی‌تواند خالی باشد.")
                return
            add_pinned_message(text)
            state_clear(uid)
            # Broadcast to all users and pin in each chat
            from ..db import get_all_pinned_messages as _get_pins
            from telebot import types as _types
            users = get_users()
            sent = 0
            pinned = 0
            all_pins = _get_pins()
            pin_id = all_pins[-1]["id"] if all_pins else None
            for u in users:
                try:
                    sent_msg = bot.send_message(u["user_id"], text, parse_mode="HTML")
                    if pin_id:
                        save_pinned_send(pin_id, u["user_id"], sent_msg.message_id)
                    sent += 1
                    try:
                        bot.pin_chat_message(u["user_id"], sent_msg.message_id, disable_notification=True)
                        pinned += 1
                    except Exception:
                        pass
                except Exception:
                    pass
            pins = _get_pins()
            kb = _types.InlineKeyboardMarkup()
            kb.add(_types.InlineKeyboardButton("➕ افزودن پیام پین", callback_data="adm:pin:add"))
            for p in pins:
                preview = (p["text"] or "")[:30].replace("\n", " ")
                kb.row(
                    _types.InlineKeyboardButton(f"📌 {preview}", callback_data="noop"),
                    _types.InlineKeyboardButton("✏️", callback_data=f"adm:pin:edit:{p['id']}"),
                    _types.InlineKeyboardButton("🗑", callback_data=f"adm:pin:del:{p['id']}"),
                )
            kb.add(_types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
            count_text = f"{len(pins)} پیام" if pins else "هیچ پیامی ثبت نشده"
            bot.send_message(uid,
                f"✅ پیام پین ارسال شد.\n📤 فرستاده شده: {sent} کاربر\n📌 پین شده: {pinned} کاربر\n\n"
                f"📌 <b>پیام‌های پین شده</b>\n\n{count_text}",
                reply_markup=kb, parse_mode="HTML")
            return

        if sn == "admin_pin_edit" and admin_has_perm(uid, "settings"):
            text = (message.text or "").strip()
            if not text:
                bot.send_message(uid, "⚠️ متن پیام نمی‌تواند خالی باشد.")
                return
            pin_id = sd.get("pin_id")
            if pin_id:
                update_pinned_message(pin_id, text)
                # Edit the sent messages in all user chats
                sends = get_pinned_sends(pin_id)
                edited = 0
                for s in sends:
                    try:
                        bot.edit_message_text(text, s["user_id"], s["message_id"], parse_mode="HTML")
                        edited += 1
                    except Exception:
                        pass
            state_clear(uid)
            from ..db import get_all_pinned_messages as _get_pins
            from telebot import types as _types
            pins = _get_pins()
            kb = _types.InlineKeyboardMarkup()
            kb.add(_types.InlineKeyboardButton("➕ افزودن پیام پین", callback_data="adm:pin:add"))
            for p in pins:
                preview = (p["text"] or "")[:30].replace("\n", " ")
                kb.row(
                    _types.InlineKeyboardButton(f"📌 {preview}", callback_data="noop"),
                    _types.InlineKeyboardButton("✏️", callback_data=f"adm:pin:edit:{p['id']}"),
                    _types.InlineKeyboardButton("🗑", callback_data=f"adm:pin:del:{p['id']}"),
                )
            kb.add(_types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
            count_text = f"{len(pins)} پیام" if pins else "هیچ پیامی ثبت نشده"
            edited_count = edited if pin_id else 0
            bot.send_message(uid,
                f"✅ پیام پین ویرایش شد.\n✏️ آپدیت شده: {edited_count} کاربر\n\n"
                f"📌 <b>پیام‌های پین شده</b>\n\n{count_text}",
                reply_markup=kb, parse_mode="HTML")
            return

        # ── Panel: Add Traffic Package (multi-step) ────────────────────────────
        if sn == "panel_pkg_add_name" and is_admin(uid):
            name = (message.text or "").strip()
            if not name:
                bot.send_message(uid, "⚠️ Package name cannot be empty."); return
            state_set(uid, "panel_pkg_add_vol", panel_id=sd["panel_id"], name=name)
            bot.send_message(uid, "📦 Step 2/3: Enter Volume in <b>GB</b> (e.g. 50):")
            return

        if sn == "panel_pkg_add_vol" and is_admin(uid):
            raw = (message.text or "").strip()
            if not raw.isdigit() or int(raw) <= 0:
                bot.send_message(uid, "⚠️ Enter a valid number of GB."); return
            state_set(uid, "panel_pkg_add_days", panel_id=sd["panel_id"],
                      name=sd["name"], volume_gb=raw)
            bot.send_message(uid, "⏰ Step 3/4: Enter Duration in <b>Days</b> (e.g. 30):")
            return

        if sn == "panel_pkg_add_days" and is_admin(uid):
            raw = (message.text or "").strip()
            if not raw.isdigit() or int(raw) <= 0:
                bot.send_message(uid, "⚠️ Enter a valid number of days."); return
            state_set(uid, "panel_pkg_add_inbound", panel_id=sd["panel_id"],
                      name=sd["name"], volume_gb=sd["volume_gb"], duration_days=raw)
            bot.send_message(uid, "🔢 Step 4/4: Enter <b>Inbound ID</b> (the numeric ID of the inbound in 3x-ui, e.g. 1):")
            return

        if sn == "panel_pkg_add_inbound" and is_admin(uid):
            raw = (message.text or "").strip()
            if not raw.isdigit() or int(raw) <= 0:
                bot.send_message(uid, "⚠️ Enter a valid inbound ID (number)."); return
            state_clear(uid)
            pp_id = add_panel_package(sd["panel_id"], sd["name"], int(sd["volume_gb"]),
                                      int(sd["duration_days"]), int(raw))
            bot.send_message(uid,
                f"✅ <b>Package Added!</b>\n\n"
                f"📦 Name: {esc(sd['name'])}\n"
                f"🔋 Volume: {sd['volume_gb']} GB\n"
                f"⏰ Duration: {sd['duration_days']} days\n"
                f"🔢 Inbound ID: #{raw}\n"
                f"🆔 Package ID: #{pp_id}",
                reply_markup=kb_admin_panel(uid))
            return

        # ── Panel: Edit field ──────────────────────────────────────────────────
        if sn == "panel_edit_field" and is_admin(uid):
            value    = (message.text or "").strip()
            field    = sd["field"]
            panel_id = sd["panel_id"]
            if not value:
                bot.send_message(uid, "⚠️ Value cannot be empty."); return
            if field == "port" and not value.isdigit():
                bot.send_message(uid, "⚠️ Port must be numeric."); return
            update_panel_field(panel_id, field, int(value) if field == "port" else value)
            state_clear(uid)
            bot.send_message(uid, f"✅ Field <b>{field}</b> updated.", reply_markup=kb_admin_panel(uid))
            return

        # ── Panel: Set Worker API Key ──────────────────────────────────────────
        if sn == "panel_set_api_key" and is_admin(uid):
            key = (message.text or "").strip()
            if len(key) < 16 or not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
                bot.send_message(uid, "⚠️ API key must be at least 16 alphanumeric characters."); return
            setting_set("worker_api_key", key)
            state_clear(uid)
            bot.send_message(uid, f"✅ Worker API key saved.\n\n🔑 <code>{esc(key)}</code>",
                             reply_markup=kb_admin_panel(uid))
            return

        # ── Panel: Set Worker API Port ─────────────────────────────────────────
        if sn == "panel_set_api_port" and is_admin(uid):
            raw = (message.text or "").strip()
            if not raw.isdigit() or not (1 <= int(raw) <= 65535):
                bot.send_message(uid, "⚠️ Enter a valid port number (1-65535)."); return
            setting_set("worker_api_port", raw)
            state_clear(uid)
            bot.send_message(uid, f"✅ API port set to <b>{raw}</b>.", reply_markup=kb_admin_panel(uid))
            return

    except Exception as e:
        print("TEXT_HANDLER_ERROR:", e)
        traceback.print_exc()
        state_clear(uid)
        bot.send_message(uid, "⚠️ خطایی رخ داد. لطفاً دوباره از منو ادامه دهید.", reply_markup=kb_main(uid))
        return

    # Fallback
    if message.content_type == "text":
        if message.text == "/start":
            return
        bot.send_message(uid, "لطفاً از دکمه‌های منو استفاده کنید.", reply_markup=kb_main(uid))

