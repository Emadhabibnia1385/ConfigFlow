# -*- coding: utf-8 -*-
"""
Core Telegram UI helpers: message editing/sending, bot commands,
channel lock enforcement.
"""
from telebot import types

from ..db import setting_get
from ..bot_instance import bot


# ── Bot commands ───────────────────────────────────────────────────────────────
def set_bot_commands():
    try:
        bot.set_my_commands([types.BotCommand("start", "شروع ربات")])
    except Exception:
        pass


# ── Message send/edit ──────────────────────────────────────────────────────────
def send_or_edit(call_or_msg, text, reply_markup=None, disable_preview=True):
    """Edit an existing message (from a callback) or send a new one."""
    try:
        if hasattr(call_or_msg, "message"):
            bot.edit_message_text(
                text,
                call_or_msg.message.chat.id,
                call_or_msg.message.message_id,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_preview,
            )
        else:
            bot.send_message(
                call_or_msg.chat.id, text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_preview
            )
    except Exception:
        try:
            chat_id = (
                call_or_msg.message.chat.id
                if hasattr(call_or_msg, "message")
                else call_or_msg.chat.id
            )
            bot.send_message(chat_id, text,
                             reply_markup=reply_markup,
                             disable_web_page_preview=disable_preview)
        except Exception:
            pass


# ── Channel lock ───────────────────────────────────────────────────────────────
def check_channel_membership(user_id):
    channel_id = setting_get("channel_id", "").strip()
    if not channel_id:
        return True
    try:
        member = bot.get_chat_member(channel_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return True


def channel_lock_message(target):
    channel_id = setting_get("channel_id", "").strip()
    kb = types.InlineKeyboardMarkup()
    if channel_id.startswith("@"):
        channel_url = f"https://t.me/{channel_id.lstrip('@')}"
    elif channel_id.startswith("-100"):
        channel_url = f"https://t.me/c/{channel_id[4:]}"
    else:
        channel_url = f"https://t.me/{channel_id}"
    kb.add(types.InlineKeyboardButton("📢 عضویت در کانال", url=channel_url))
    kb.add(types.InlineKeyboardButton("✅ عضو شدم", callback_data="check_channel"))
    send_or_edit(
        target,
        "🔒 برای استفاده از ربات، ابتدا باید در کانال ما عضو شوید.\n\nپس از عضویت، روی «عضو شدم» بزنید.",
        kb
    )
