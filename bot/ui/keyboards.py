# -*- coding: utf-8 -*-
"""
Inline keyboard builders for main menu and admin panel.
"""
from telebot import types

from ..config import ADMIN_IDS, PERM_USER_FULL
from ..db import setting_get
from ..helpers import is_admin, admin_has_perm


def kb_main(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("🛒 خرید کانفیگ جدید", callback_data="buy:start"),
        types.InlineKeyboardButton("📦 کانفیگ‌های من",    callback_data="my_configs"),
    )
    if setting_get("referral_enabled", "1") == "1":
        kb.add(types.InlineKeyboardButton("🎁 تست رایگان", callback_data="test:start"))
    kb.row(
        types.InlineKeyboardButton("👤 حساب کاربری",    callback_data="profile"),
        types.InlineKeyboardButton("💳 شارژ کیف پول",   callback_data="wallet:charge"),
    )
    ref_on     = setting_get("referral_enabled", "1") == "1"
    voucher_on = setting_get("vouchers_enabled", "1") == "1"
    if ref_on and voucher_on:
        kb.row(
            types.InlineKeyboardButton("🎁 دعوت دوستان",    callback_data="referral:menu"),
            types.InlineKeyboardButton("🎫 ثبت کارت هدیه", callback_data="voucher:redeem"),
        )
    elif ref_on:
        kb.add(types.InlineKeyboardButton("🎁 دعوت دوستان",    callback_data="referral:menu"))
    elif voucher_on:
        kb.add(types.InlineKeyboardButton("🎫 ثبت کارت هدیه", callback_data="voucher:redeem"))
    kb.add(types.InlineKeyboardButton("🎧 ارتباط با پشتیبانی", callback_data="support"))
    if setting_get("agency_request_enabled", "1") == "1":
        kb.add(types.InlineKeyboardButton("🤝 درخواست نمایندگی", callback_data="agency:request"))
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("⚙️ ورود به پنل مدیریت", callback_data="admin:panel"))
    return kb


def kb_admin_panel(uid=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    is_owner = (uid in ADMIN_IDS) if uid else False

    if is_owner or (uid and admin_has_perm(uid, "types_packages")):
        kb.row(types.InlineKeyboardButton("🧩 مدیریت نوع و پکیج‌ها", callback_data="admin:types"))

    if is_owner or (uid and (admin_has_perm(uid, "view_configs") or
                             admin_has_perm(uid, "register_config") or
                             admin_has_perm(uid, "manage_configs"))):
        kb.row(types.InlineKeyboardButton("📚 کانفیگ‌ها", callback_data="admin:stock"))

    show_users = is_owner or (uid and (admin_has_perm(uid, "view_users") or
                                       admin_has_perm(uid, "full_users") or
                                       any(admin_has_perm(uid, p) for p in PERM_USER_FULL)))
    show_agents = is_owner or (uid and admin_has_perm(uid, "agency"))

    if show_users and is_owner:
        kb.row(
            types.InlineKeyboardButton("👥 مدیریت کاربران",  callback_data="admin:users"),
            types.InlineKeyboardButton("👮 مدیریت ادمین‌ها", callback_data="admin:admins"),
        )
        if show_agents:
            kb.row(types.InlineKeyboardButton("🤝 مدیریت نمایندگان", callback_data="admin:agents"))
    elif show_users:
        if show_agents:
            kb.row(
                types.InlineKeyboardButton("👥 مدیریت کاربران", callback_data="admin:users"),
                types.InlineKeyboardButton("🤝 مدیریت نمایندگان", callback_data="admin:agents"),
            )
        else:
            kb.row(types.InlineKeyboardButton("👥 مدیریت کاربران", callback_data="admin:users"))
    elif is_owner:
        kb.row(
            types.InlineKeyboardButton("👮 مدیریت ادمین‌ها", callback_data="admin:admins"),
            types.InlineKeyboardButton("🤝 مدیریت نمایندگان", callback_data="admin:agents"),
        )
    elif show_agents:
        kb.row(types.InlineKeyboardButton("🤝 مدیریت نمایندگان", callback_data="admin:agents"))

    if is_owner:
        kb.row(
            types.InlineKeyboardButton("🎟 کد تخفیف",  callback_data="admin:discounts"),
            types.InlineKeyboardButton("🎁 کارت هدیه", callback_data="admin:vouchers"),
        )

    show_broadcast = is_owner or (uid and (admin_has_perm(uid, "broadcast_all") or admin_has_perm(uid, "broadcast_cust")))
    show_pr        = is_owner or (uid and admin_has_perm(uid, "approve_payments"))
    if show_broadcast and show_pr:
        kb.row(
            types.InlineKeyboardButton("📣 فوروارد همگانی",        callback_data="admin:broadcast"),
            types.InlineKeyboardButton("📋 رسیدهای بررسی نشده", callback_data="admin:pr"),
        )
    elif show_broadcast:
        kb.add(types.InlineKeyboardButton("📣 فوروارد همگانی", callback_data="admin:broadcast"))
    elif show_pr:
        kb.add(types.InlineKeyboardButton("📋 رسیدهای بررسی نشده", callback_data="admin:pr"))

    if is_owner or (uid and admin_has_perm(uid, "manage_panels")):
        kb.add(types.InlineKeyboardButton("🖥 مدیریت پنل‌های 3x-ui", callback_data="admin:panels"))

    if is_owner or (uid and admin_has_perm(uid, "settings")):
        kb.add(types.InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin:settings"))

    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    return kb
