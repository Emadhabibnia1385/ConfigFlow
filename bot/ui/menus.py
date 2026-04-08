# -*- coding: utf-8 -*-
"""
Main menu screens: home, profile, support, my configs.
"""
import urllib.parse
from telebot import types

from ..config import BRAND_TITLE, DEFAULT_ADMIN_HANDLE
from ..db import setting_get, get_user, get_user_purchases, get_referral_stats
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
    kb.add(types.InlineKeyboardButton(" بازگشت", callback_data="nav:main"))
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
        svc_name     = urllib.parse.unquote(item["service_name"] or "")
        title        = f"{svc_name}{expired_mark}"
        kb.add(types.InlineKeyboardButton(title, callback_data=f"mycfg:{item['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, "📦 <b>کانفیگ‌های من</b>\n\nیکی از سرویس‌ها را انتخاب کنید:", kb)


def show_referral_menu(target, user_id):
    """Show referral/invite page with stats and share button."""
    if setting_get("referral_enabled", "1") != "1":
        send_or_edit(target,
            "⚠️ <b>سیستم دعوت دوستان</b>\n\n"
            "در حال حاضر سیستم زیرمجموعه‌گیری برای این ربات فعال نشده است.\n"
            "لطفاً بعداً مراجعه کنید یا با پشتیبانی تماس بگیرید.",
            back_button("main"))
        return

    stats = get_referral_stats(user_id)
    bot_info = bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    # Build reward info
    start_reward_enabled = setting_get("referral_start_reward_enabled", "0") == "1"
    purchase_reward_enabled = setting_get("referral_purchase_reward_enabled", "0") == "1"

    reward_text = ""
    if start_reward_enabled:
        sr_type = setting_get("referral_start_reward_type", "wallet")
        sr_count = setting_get("referral_start_reward_count", "1")
        if sr_type == "wallet":
            sr_amount = setting_get("referral_start_reward_amount", "0")
            reward_text += f"🎁 <b>هدیه عضویت:</b> به ازای هر {sr_count} زیرمجموعه، <b>{fmt_price(int(sr_amount))}</b> تومان شارژ کیف پول\n"
        else:
            reward_text += f"🎁 <b>هدیه عضویت:</b> به ازای هر {sr_count} زیرمجموعه، یک کانفیگ رایگان\n"

    if purchase_reward_enabled:
        pr_type = setting_get("referral_purchase_reward_type", "wallet")
        pr_count = setting_get("referral_purchase_reward_count", "1")
        if pr_type == "wallet":
            pr_amount = setting_get("referral_purchase_reward_amount", "0")
            reward_text += f"💸 <b>هدیه خرید:</b> به ازای هر {pr_count} خرید زیرمجموعه، <b>{fmt_price(int(pr_amount))}</b> تومان شارژ کیف پول\n"
        else:
            reward_text += f"💸 <b>هدیه خرید:</b> به ازای هر {pr_count} خرید زیرمجموعه، یک کانفیگ رایگان\n"

    if not reward_text:
        reward_text = "🎁 هدیه‌ها هنوز توسط ادمین تنظیم نشده است.\n"

    text = (
        "💼 <b>زیرمجموعه‌گیری و دعوت دوستان</b>\n\n"
        "با دعوت دوستان از طریق لینک اختصاصی، بدون پرداخت حتی ۱ ریال "
        "کیف پولت شارژ می‌شه و از خدمات ربات استفاده می‌کنی! 🎉\n\n"
        f"{reward_text}\n"
        "📊 <b>آمار شما:</b>\n"
        f"  👥 زیرمجموعه‌ها: <b>{stats['total_referrals']}</b> نفر\n"
        f"  🛒 خریدهای زیرمجموعه: <b>{stats['purchase_count']}</b> عدد\n"
        f"  💵 مجموع خرید زیرمجموعه: <b>{fmt_price(stats['total_purchase_amount'])}</b> تومان\n\n"
        f"🔗 <b>لینک دعوت شما:</b>\n<code>{ref_link}</code>\n\n"
        "📢 <b>دعوت کن، هدیه بگیر، رشد کن!</b>"
    )

    # Build share text
    custom_banner = setting_get("referral_banner_text", "").strip()
    if custom_banner:
        share_text = f"{custom_banner}\n\n👇 از لینک زیر وارد شو:\n{ref_link}"
    else:
        share_text = (
            f"🔥 می‌خوای با سرعت بالا و پایداری عالی به اینترنت آزاد وصل بشی؟\n\n"
            f"من از {BRAND_TITLE} سرویس VPN خریدم و کاملاً راضیم! 😍\n\n"
            f"✅ سرعت فوق‌العاده\n"
            f"✅ پایداری بالا\n"
            f"✅ پشتیبانی ۲۴ ساعته\n\n"
            f"تو هم از لینک من وارد شو و سرویست رو بخر 👇\n{ref_link}"
        )

    import urllib.parse as _up
    share_url = f"https://t.me/share/url?url={_up.quote(ref_link)}&text={_up.quote(share_text)}"

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📤 اشتراک‌گذاری لینک دعوت", url=share_url))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, text, kb)
