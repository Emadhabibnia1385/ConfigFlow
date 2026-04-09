# -*- coding: utf-8 -*-
"""
Database backup: send the SQLite DB file to a target chat on a schedule.
"""
import time
from datetime import datetime

from ..config import DB_NAME
from ..db import setting_get
from ..helpers import esc
from ..bot_instance import bot
from ..group_manager import send_document_to_topic


def _send_backup(target_chat_id):
    try:
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        caption = f"🗄 بکاپ دیتابیس\n\n📦 ConfigFlow_backup_{ts}.db"
        fname   = f"ConfigFlow_backup_{ts}.db"
        with open(DB_NAME, "rb") as f:
            bot.send_document(
                target_chat_id, f,
                caption=caption,
                visible_file_name=fname
            )
        with open(DB_NAME, "rb") as f:
            send_document_to_topic("backup", f, caption=caption, visible_file_name=fname)
    except Exception as e:
        try:
            bot.send_message(target_chat_id, f"❌ خطا در ارسال بکاپ: {esc(str(e))}")
        except Exception:
            pass


def _backup_loop():
    last_backup_at = 0.0  # unix timestamp of last successful backup
    while True:
        time.sleep(60)  # check every minute
        try:
            enabled  = setting_get("backup_enabled", "0")
            interval = int(setting_get("backup_interval", "24") or "24")
            target   = setting_get("backup_target_id", "").strip()
            if enabled != "1" or not target:
                continue
            now = time.time()
            if now - last_backup_at >= interval * 3600:
                _send_backup(int(target) if target.lstrip("-").isdigit() else target)
                last_backup_at = now
        except Exception:
            pass
