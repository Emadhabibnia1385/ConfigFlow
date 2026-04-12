# -*- coding: utf-8 -*-
"""
Database backup: send the SQLite DB file to a target chat on a schedule.
"""
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime

from ..config import DB_NAME
from ..db import setting_get
from ..helpers import esc
from ..bot_instance import bot
from ..group_manager import send_document_to_topic


# ── Service-name detection ─────────────────────────────────────────────────────

def _get_own_service_name():
    """
    Detect the systemd service name that is running this process.
    Reads /proc/self/cgroup (Linux only); returns None on any failure.
    """
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                # e.g.  0::/system.slice/seamless-1.service
                if ".service" in line:
                    part = line.strip().rsplit("/", 1)[-1]
                    if part.endswith(".service"):
                        return part[:-8]   # strip ".service"
    except Exception:
        pass

    # Fallback: infer from working-directory pattern /opt/<service>-N/
    try:
        cwd = os.getcwd()
        m = re.search(r'/opt/((?:configflow|seamless)[-_][^/]+)', cwd)
        if m:
            return m.group(1)
    except Exception:
        pass

    return None


# ── Safe DB restore ────────────────────────────────────────────────────────────

def safe_restore_db(db_content_bytes, db_filename=None):
    """
    Safely restore a SQLite backup for the running bot.

    Steps:
    1. Validate SQLite file header.
    2. Write to a temp file and run PRAGMA integrity_check.
    3. Verify required tables are present.
    4. Back up the current (possibly corrupted) DB.
    5. Remove stale WAL / SHM side-car files.
    6. Atomically replace the live DB.
    7. Schedule a systemd service restart via a background thread so the
       Telegram response is delivered before the process is killed.

    Returns (success: bool, message: str).
    """
    # ── 1. Header check ──────────────────────────────────────────────────────
    if len(db_content_bytes) < 16 or db_content_bytes[:16] != b"SQLite format 3\x00":
        return False, "فایل ارسالی دیتابیس SQLite معتبر نیست."

    # ── 2. Write temp & integrity check ─────────────────────────────────────
    tmp_path = DB_NAME + ".tmp_restore"
    try:
        with open(tmp_path, "wb") as f:
            f.write(db_content_bytes)

        test_conn = sqlite3.connect(tmp_path)
        try:
            row = test_conn.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                return False, "فایل بکاپ خراب است و integrity check را رد کرد."

            # ── 3. Required tables ───────────────────────────────────────────
            tables = {r[0] for r in test_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required = {"users", "config_types", "packages", "configs"}
            missing  = required - tables
            if missing:
                return False, f"بکاپ ناقص است. جدول‌های ناموجود: {', '.join(sorted(missing))}"
            # ── 3b. Flush WAL into the temp file so nothing is lost ──────────
            # If the backup was taken as a raw file copy, any un-checkpointed
            # WAL records would be lost when we delete the live WAL sidecar.
            # Running checkpoint here merges them into tmp_path itself.
            test_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            test_conn.commit()
        finally:
            test_conn.close()

        # ── 4. Backup current DB (best-effort; it may already be corrupted) ──
        backup_path = None
        if os.path.isfile(DB_NAME):
            ts = int(time.time())
            backup_path = DB_NAME + f".bak_{ts}"
            try:
                shutil.copy2(DB_NAME, backup_path)
            except Exception:
                backup_path = None

        # ── 5. Remove stale WAL / SHM ────────────────────────────────────────
        for sidecar in (DB_NAME + "-wal", DB_NAME + "-shm"):
            try:
                if os.path.exists(sidecar):
                    os.remove(sidecar)
            except Exception:
                pass

        # ── 6. Atomic replace ────────────────────────────────────────────────
        os.replace(tmp_path, DB_NAME)

        # ── 7. Schedule restart ──────────────────────────────────────────────
        svc = _get_own_service_name()
        if svc:
            def _restart():
                time.sleep(2)          # let Telegram deliver the reply first
                subprocess.run(["systemctl", "restart", svc], check=False)
            threading.Thread(target=_restart, daemon=True).start()
            restart_note = "\n🔄 ربات در حال ریستارت است…"
        else:
            restart_note = "\n⚠️ سرویس شناسایی نشد — ربات را دستی ریستارت کنید."

        bkp_note = (
            f"\n💾 نسخه قبلی در <code>{esc(backup_path)}</code> ذخیره شد."
            if backup_path else ""
        )
        return True, f"دیتابیس با موفقیت ری‌استور شد.{bkp_note}{restart_note}"

    except Exception as e:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False, f"خطا در ری‌استور: {esc(str(e))}"


def _send_backup(target_chat_id):
    tmp_backup = DB_NAME + ".backup_send_tmp"
    try:
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        caption = f"🗄 بکاپ دیتابیس\n\n📦 ConfigFlow_backup_{ts}.db"
        fname   = f"ConfigFlow_backup_{ts}.db"

        # Use SQLite online backup API to get a consistent snapshot that
        # includes all WAL data, even if the bot is actively writing.
        import io
        src_conn = sqlite3.connect(DB_NAME)
        dst_conn = sqlite3.connect(tmp_backup)
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        with open(tmp_backup, "rb") as f:
            data = f.read()

        buf1 = io.BytesIO(data)
        buf1.name = fname
        bot.send_document(
            target_chat_id, buf1,
            caption=caption,
            visible_file_name=fname
        )
        buf2 = io.BytesIO(data)
        buf2.name = fname
        send_document_to_topic("backup", buf2, caption=caption, visible_file_name=fname)
    except Exception as e:
        try:
            bot.send_message(target_chat_id, f"❌ خطا در ارسال بکاپ: {esc(str(e))}")
        except Exception:
            pass
    finally:
        try:
            if os.path.isfile(tmp_backup):
                os.remove(tmp_backup)
            for sidecar in (tmp_backup + "-wal", tmp_backup + "-shm"):
                if os.path.isfile(sidecar):
                    os.remove(sidecar)
        except Exception:
            pass


def _backup_loop():
    last_backup_at = 0.0  # unix timestamp of last successful backup
    while True:
        time.sleep(600)  # check every 10 min
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
