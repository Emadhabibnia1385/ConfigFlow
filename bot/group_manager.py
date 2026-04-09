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
    ("broadcast_report", "📢 اطلاع‌رسانی و پین"),
    ("referral_log",     "🔗 لاگ زیرمجموعه‌گیری"),
    ("agency_request",   "🤝 درخواست نمایندگی"),
    ("agency_log",       "🏢 لاگ نمایندگان"),
    ("admin_ops_log",    "📝 لاگ عملیاتی"),
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
    """Validate existing topics (remove broken ones), then create any missing ones."""
    group_id = get_group_id()
    if group_id:
        for key, name in TOPICS:
            tid = _get_topic_id(key)
            if not tid:
                continue
            # Try a no-op edit to confirm the topic still exists in Telegram
            try:
                bot.edit_forum_topic(group_id, tid, name=name)
            except Exception as e:
                err = str(e)
                if any(x in err for x in ("TOPIC_DELETED", "TOPIC_ID_INVALID",
                                           "not found", "thread", "MESSAGE_THREAD")):
                    setting_set(_SETTING_KEY[key], "")
                # For other errors (e.g. permissions) keep the stored ID
    return ensure_group_topics()


# ── Send helpers ───────────────────────────────────────────────────────────────
def send_to_topic(topic_key, text, parse_mode="HTML", reply_markup=None):
    """Send a text message to the specified topic. Returns Message or None."""
    # Check if this notification type is enabled for group
    if setting_get(f"notif_grp_{topic_key}", "1") != "1":
        return None
    group_id = get_group_id()
    if not group_id:
        return None
    thread_id = _get_topic_id(topic_key)
    if not thread_id:
        return None
    try:
        return bot.send_message(
            group_id, text,
            message_thread_id=thread_id,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception:
        return None


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


def send_document_to_topic(topic_key, document, caption=None, visible_file_name=None):
    """Send a document to the specified topic. Silent on any error."""
    group_id = get_group_id()
    if not group_id:
        return
    thread_id = _get_topic_id(topic_key)
    if not thread_id:
        return
    try:
        kwargs = dict(
            message_thread_id=thread_id,
            caption=caption,
            parse_mode="HTML",
        )
        if visible_file_name:
            kwargs["visible_file_name"] = visible_file_name
        bot.send_document(group_id, document, **kwargs)
    except Exception:
        pass


# ── Admin operation log helper ─────────────────────────────────────────────────
def log_admin_action(admin_id, action_text):
    """Log an admin operation to the admin_ops_log topic."""
    send_to_topic("admin_ops_log",
        f"📝 <b>عملیات مدیریتی</b>\n\n"
        f"👤 اجراکننده: <code>{admin_id}</code>\n"
        f"📌 عملیات: {action_text}"
    )


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
