# -*- coding: utf-8 -*-
"""
Core Telegram UI helpers: message editing/sending, bot commands,
license gate, and multi-channel forced-join enforcement.
"""
import json
import logging
import time

from telebot import types

from ..db import setting_get
from ..bot_instance import bot

logger = logging.getLogger(__name__)

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


# ── License Gate ───────────────────────────────────────────────────────────────
# The bot MUST be a member/admin of this channel for the license to be considered active.
LICENSE_CHANNEL_ID       = -1002261879501
LICENSE_CHANNEL_USERNAME = "@EmadHabibnia"
_LICENSE_CACHE_OK_TTL   = 300  # cache successful checks for 5 minutes
_LICENSE_CACHE_FAIL_TTL = 15   # cache failed checks for only 15 seconds (re-check quickly)

_license_cache: dict = {
    "ok":              None,   # True/False/None (None = not yet checked)
    "checked_at":      0.0,
    "owner_notified":  False,  # reset whenever status flips to False
}


def check_license_gate() -> bool:
    """
    Return True if the bot is a member/admin of the license channel.
    Successful results are cached for 5 minutes; failed results only 15 seconds
    so the bot recovers quickly after being added to the channel.
    """
    now = time.time()
    if _license_cache["ok"] is not None:
        ttl = _LICENSE_CACHE_OK_TTL if _license_cache["ok"] else _LICENSE_CACHE_FAIL_TTL
        if now - _license_cache["checked_at"] < ttl:
            return bool(_license_cache["ok"])

    try:
        me     = bot.get_me()
        member = bot.get_chat_member(LICENSE_CHANNEL_ID, me.id)
        ok     = member.status in ("member", "administrator", "creator")
    except Exception as exc:
        logger.warning("License gate check failed: %s", exc)
        ok = False

    prev_ok = _license_cache["ok"]
    _license_cache["ok"]         = ok
    _license_cache["checked_at"] = now

    # When transitioning to "not OK" (including first-time fail), reset notify flag
    if not ok and (prev_ok is None or prev_ok is True):
        _license_cache["owner_notified"] = False

    return ok


def notify_owner_license_fail():
    """
    Send the 'license not active' notification to all owners.
    Called at most once per failure period (tracked by _license_cache["owner_notified"]).
    """
    if _license_cache.get("owner_notified"):
        return
    from ..config import ADMIN_IDS
    for owner_id in ADMIN_IDS:
        try:
            bot.send_message(
                owner_id,
                "⚠️ <b>لایسنس رایگان ربات شما فعال نیست.</b>\n\n"
                "برای فعال‌سازی به @Emad_Habibnia پیام دهید.",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not notify owner %s about license fail: %s", owner_id, exc)
    _license_cache["owner_notified"] = True


def send_license_fail_to_target(target):
    """
    Reply to a message/callback with the appropriate license-fail text.
    Owners see the license prompt; regular users see a generic 'not active' message.
    """
    from ..config import ADMIN_IDS
    if hasattr(target, "from_user"):
        uid     = target.from_user.id
        chat_id = target.message.chat.id if hasattr(target, "message") and target.message else target.chat.id
    else:
        uid     = getattr(target, "from_user", None)
        chat_id = target.chat.id

    if uid in ADMIN_IDS:
        text = (
            "⚠️ <b>لایسنس رایگان ربات شما فعال نیست.</b>\n\n"
            "برای فعال‌سازی به @Emad_Habibnia پیام دهید."
        )
    else:
        text = (
            "⛔️ <b>ربات در حال حاضر فعال نیست.</b>\n\n"
            "بعداً دوباره تلاش کنید."
        )
    try:
        bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        pass


# ── Forced Join (multi-channel) ────────────────────────────────────────────────

def get_forced_channels() -> list:
    """
    Return ALL forced channels as a list of dicts:
        {"name": str, "id": str, "username": str}

    Combines:
      • legacy `channel_id` setting (single channel, backward compat)
      • new `forced_channels` setting (JSON array of channel dicts)
    """
    channels: list = []

    # Legacy single-channel setting
    old_cid = setting_get("channel_id", "").strip()
    if old_cid:
        channels.append({
            "name":     "کانال ما",
            "id":       old_cid,
            "username": old_cid if old_cid.startswith("@") else "",
        })

    # New multi-channel JSON setting
    try:
        extra = json.loads(setting_get("forced_channels", "[]") or "[]")
        if isinstance(extra, list):
            for ch in extra:
                if isinstance(ch, dict) and (ch.get("id") or ch.get("username")):
                    channels.append(ch)
    except Exception:
        pass

    return channels


def _channel_join_url(ch: dict) -> str:
    """Build a t.me invite URL for a channel dict."""
    ch_id    = str(ch.get("id")       or "").strip()
    username = str(ch.get("username") or "").strip()
    if username.startswith("@"):
        return f"https://t.me/{username.lstrip('@')}"
    if ch_id.startswith("@"):
        return f"https://t.me/{ch_id.lstrip('@')}"
    if ch_id.startswith("-100"):
        return f"https://t.me/c/{ch_id[4:]}"
    return f"https://t.me/{ch_id}"


def _is_member(ch_id_str: str, user_id: int) -> bool:
    """Check membership of user_id in the given channel id/username string."""
    try:
        member = bot.get_chat_member(ch_id_str, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as exc:
        logger.debug("Membership check error for channel %s: %s", ch_id_str, exc)
        return True  # on API error assume OK (don't block on Telegram outage)


def check_channel_membership(user_id: int) -> bool:
    """Return True only when the user is a member of ALL forced channels."""
    for ch in get_forced_channels():
        ch_id = str(ch.get("id") or ch.get("username", "")).strip()
        if not ch_id:
            continue
        if not _is_member(ch_id, user_id):
            return False
    return True


def get_unjoined_channels(user_id: int) -> list:
    """Return only the channels the user has NOT yet joined."""
    result = []
    for ch in get_forced_channels():
        ch_id = str(ch.get("id") or ch.get("username", "")).strip()
        if not ch_id:
            continue
        if not _is_member(ch_id, user_id):
            result.append(ch)
    return result


def channel_lock_message(target, uid: int = None):
    """
    Show the forced-join message listing every channel the user hasn't joined yet.
    Works for both Message objects and CallbackQuery objects.
    """
    if uid is None:
        if hasattr(target, "from_user") and target.from_user:
            uid = target.from_user.id
        elif hasattr(target, "message") and target.message and target.message.from_user:
            uid = target.message.from_user.id

    unjoined = get_unjoined_channels(uid) if uid else get_forced_channels()
    if not unjoined:
        # Fallback: list all channels
        unjoined = get_forced_channels()

    kb = types.InlineKeyboardMarkup()
    for ch in unjoined:
        name = ch.get("name") or "کانال"
        kb.add(types.InlineKeyboardButton(
            f"📢 عضویت در {name}",
            url=_channel_join_url(ch),
        ))
    kb.add(types.InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="check_channel"))

    send_or_edit(
        target,
        "🔒 <b>برای استفاده از ربات، باید در همه کانال‌های اجباری عضو شوید.</b>\n\n"
        "پس از عضویت، روی «عضو شدم» بزنید.",
        kb,
    )
