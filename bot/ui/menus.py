# -*- coding: utf-8 -*-
"""
Main menu screens: home, profile, support, my configs.
"""
from telebot import types

from ..config import BRAND_TITLE, DEFAULT_ADMIN_HANDLE
from ..db import setting_get, get_user, get_user_purchases
from ..helpers import esc, fmt_price, display_username, back_button
from ..bot_instance import bot
from .helpers import send_or_edit
from .keyboards import kb_main


def show_main_menu(target):
    uid         = target.from_user.id if hasattr(target, "from_user") else target.chat.id
    custom_text = setting_get("start_text", "")
    if custom_text:
        text = custom_text
    else:
        text = (
            f"✨ <b>به فروشگاه {BRAND_TITLE} خوش آمدید!</b>\n\n"
            "🛡 ارائه انواع سرویس‌های VPN با کیفیت عالی\n"
            "✅ تضمین امنیت ارتباطات شما\n"
            "📞 پشتیبانی حرفه‌ای ۲۴ ساعته\n\n"
            "از منوی زیر بخش مورد نظر خود را انتخاب کنید."
        )
    send_or_edit(target, text, kb_main(uid))


def show_profile(target, user_id):
    user = get_user(user_id)
    if not user:
        return
    text = (
        "👤 <b>پروفایل کاربری</b>\n\n"
        f"📱 نام: {esc(user['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
        f"🔢 آیدی: <code>{user['user_id']}</code>\n\n"
        f"💰 موجودی: <b>{fmt_price(user['balance'])}</b> تومان"
    )
    if user["is_agent"]:
        text += "\n\n🤝 <b>حساب نمایندگی فعال است</b>"
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="wallet:charge"),
        types.InlineKeyboardButton("📦 کانفیگ‌های من", callback_data="my_configs"),
    )
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, text, kb)


def show_support(target):
    support_raw      = setting_get("support_username", DEFAULT_ADMIN_HANDLE)
    from ..helpers import safe_support_url
    support_url      = safe_support_url(support_raw)
    support_link     = setting_get("support_link", "")
    support_link_desc = setting_get("support_link_desc", "")

    kb = types.InlineKeyboardMarkup()
    has_any = False
    if support_url:
        kb.add(types.InlineKeyboardButton("💬 پشتیبانی تلگرام", url=support_url))
        has_any = True
    if support_link:
        kb.add(types.InlineKeyboardButton("🌐 پشتیبانی آنلاین", url=support_link))
        has_any = True
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))

    if not has_any:
        send_or_edit(target, "⚠️ پشتیبانی هنوز تنظیم نشده است.", back_button("main"))
        return

    text = "🎧 <b>ارتباط با پشتیبانی</b>\n\n"
    if support_link_desc:
        text += f"{esc(support_link_desc)}\n\n"
    else:
        text += "از طریق یکی از روش‌های زیر با ما در ارتباط باشید.\n\n"
    send_or_edit(target, text, kb)


def show_my_configs(target, user_id):
    items = get_user_purchases(user_id)
    if not items:
        send_or_edit(target, "📭 هنوز کانفیگی برای حساب شما ثبت نشده است.", back_button("main"))
        return
    kb = types.InlineKeyboardMarkup()
    for item in items:
        expired_mark = " ❌" if item["is_expired"] else ""
        title        = f"{item['service_name']}{expired_mark}"
        kb.add(types.InlineKeyboardButton(title, callback_data=f"mycfg:{item['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, "📦 <b>کانفیگ‌های من</b>\n\nیکی از سرویس‌ها را انتخاب کنید:", kb)
