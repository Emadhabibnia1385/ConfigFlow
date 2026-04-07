# -*- coding: utf-8 -*-
"""
Group Manager — create and maintain forum topics in an admin Telegram supergroup.
All bot events are optionally mirrored to their corresponding topic.
"""
import time

from .db import setting_get, setting_set
from .bot_instance import bot

# ── Topic registry ─────────────────────────────────────────────────────────────
# Each entry: (setting_key_suffix, display_name)
TOPICS = [
    ("backup",           "💾 بکاپ"),
    ("new_users",        "👋 کاربران جدید"),
    ("payment_approval", "💳 تأیید پرداخت"),
    ("renewal_request",  "♻️ درخواست تمدید"),
    ("purchase_log",     "📦 لاگ خرید"),
    ("renewal_log",      "🔄 لاگ تمدید"),
    ("wallet_log",       "💰 لاگ کیف‌پول"),
    ("test_report",      "🧪 گزارش تست"),
    ("broadcast_report", "📢 اطلاع‌رسانی"),
    ("error_log",        "❌ گزارش خطا"),
]

_SETTING_KEY = {key: f"group_topic_{key}" for key, _ in TOPICS}


# ── Helpers ────────────────────────────────────────────────────────────────────
def get_group_id():
    val = setting_get("group_id", "").strip()
    if val and val.lstrip("-").isdigit():
        return int(val)
    return None


def _get_topic_id(topic_key):
    val = setting_get(_SETTING_KEY[topic_key], "").strip()
    if val and val.isdigit():
        return int(val)
    return None


def _count_active_topics():
    return sum(1 for key, _ in TOPICS if _get_topic_id(key))


# ── Topic creation ─────────────────────────────────────────────────────────────
def ensure_group_topics():
    """Create any missing forum topics. Returns a human-readable status string."""
    group_id = get_group_id()
    if not group_id:
        return "⚠️ آیدی گروه تنظیم نشده است."

    created    = []
    already    = []
    errors     = []
    migrated   = False

    for key, name in TOPICS:
        if _get_topic_id(key):
            already.append(name)
            continue
        try:
            topic = bot.create_forum_topic(group_id, name)
            setting_set(_SETTING_KEY[key], str(topic.message_thread_id))
            created.append(name)
        except Exception as e:
            err_str = str(e)
            # Auto-migrate: regular group upgraded to supergroup → ID changes to -100XXXXXXX
            if "upgraded to a supergroup" in err_str and not migrated:
                new_id = int(f"-100{abs(group_id)}")
                setting_set("group_id", str(new_id))
                group_id = new_id
                migrated = True
                # Retry with new ID
                try:
                    topic = bot.create_forum_topic(group_id, name)
                    setting_set(_SETTING_KEY[key], str(topic.message_thread_id))
                    created.append(name)
                except Exception as e2:
                    errors.append(f"{name} ({e2})")
            elif "upgraded to a supergroup" in err_str and migrated:
                # Already migrated, just retry
                try:
                    topic = bot.create_forum_topic(group_id, name)
                    setting_set(_SETTING_KEY[key], str(topic.message_thread_id))
                    created.append(name)
                except Exception as e2:
                    errors.append(f"{name} ({e2})")
            else:
                errors.append(f"{name} ({e})")

    parts = []
    if migrated:
        parts.append(f"🔄 آیدی گروه به سوپرگروه آپدیت شد: <code>{group_id}</code>")
    if created:
        parts.append("✅ تاپیک‌های جدید ساخته شد:\n" + "\n".join(f"  • {n}" for n in created))
    if already:
        parts.append(f"✔️ {len(already)} تاپیک از قبل موجود بود.")
    if errors:
        parts.append("❌ خطا در ساخت:\n" + "\n".join(f"  • {e}" for e in errors))
    if not created and not errors and not migrated:
        parts.append("✅ همه تاپیک‌ها موجود هستند.")
    return "\n\n".join(parts)


def reset_and_recreate_topics():
    """Clear all stored topic IDs then recreate them all."""
    for key, _ in TOPICS:
        setting_set(_SETTING_KEY[key], "")
    return ensure_group_topics()


# ── Send helpers ───────────────────────────────────────────────────────────────
def send_to_topic(topic_key, text, parse_mode="HTML", reply_markup=None):
    """Send a text message to the specified topic. Silent on any error."""
    # Check if this notification type is enabled for group
    if setting_get(f"notif_grp_{topic_key}", "1") != "1":
        return
    group_id = get_group_id()
    if not group_id:
        return
    thread_id = _get_topic_id(topic_key)
    if not thread_id:
        return
    try:
        bot.send_message(
            group_id, text,
            message_thread_id=thread_id,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception:
        pass


def send_photo_to_topic(topic_key, photo, caption=None):
    """Send a photo to the specified topic. Silent on any error."""
    group_id = get_group_id()
    if not group_id:
        return
    thread_id = _get_topic_id(topic_key)
    if not thread_id:
        return
    try:
        bot.send_photo(group_id, photo,
                       message_thread_id=thread_id,
                       caption=caption, parse_mode="HTML")
    except Exception:
        pass


def send_document_to_topic(topic_key, document, caption=None):
    """Send a document to the specified topic. Silent on any error."""
    group_id = get_group_id()
    if not group_id:
        return
    thread_id = _get_topic_id(topic_key)
    if not thread_id:
        return
    try:
        bot.send_document(group_id, document,
                          message_thread_id=thread_id,
                          caption=caption, parse_mode="HTML")
    except Exception:
        pass


# ── Background loop ────────────────────────────────────────────────────────────
def _group_topic_loop():
    """Every 15 minutes, ensure all configured topics still exist."""
    while True:
        time.sleep(15 * 60)
        try:
            if get_group_id():
                ensure_group_topics()
        except Exception:
            pass
