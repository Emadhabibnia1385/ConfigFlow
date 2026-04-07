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
            send_document_to_topic("backup", f, caption=caption)
    except Exception as e:
        try:
            bot.send_message(target_chat_id, f"❌ خطا در ارسال بکاپ: {esc(str(e))}")
        except Exception:
            pass


def _backup_loop():
    while True:
        try:
            enabled  = setting_get("backup_enabled", "0")
            interval = int(setting_get("backup_interval", "24") or "24")
            target   = setting_get("backup_target_id", "").strip()
            if enabled == "1" and target:
                _send_backup(int(target) if target.lstrip("-").isdigit() else target)
        except Exception:
            pass
        # Sleep interval hours, but re-check every minute for setting changes
        current_interval = int(setting_get("backup_interval", "24") or "24")
        for _ in range(current_interval * 60):
            time.sleep(60)
            new_interval = int(setting_get("backup_interval", "24") or "24")
            if new_interval != current_interval:
                break
