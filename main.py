# -*- coding: utf-8 -*-
"""
Entry point for the ConfigFlow Telegram Bot.

Run with:  python main.py
"""
import threading

from bot.db import init_db
from bot.db import cleanup_stale_reservations
from bot.ui.helpers import set_bot_commands
from bot.db import setting_get
from bot.admin.backup import _backup_loop
from bot.group_manager import _group_topic_loop
import bot.handlers  # noqa: F401 — registers all handlers
from bot.bot_instance import bot  # must come after to avoid being shadowed by the package name


def main():
    init_db()
    cleanup_stale_reservations()
    set_bot_commands()

    # Start backup thread
    backup_thread = threading.Thread(target=_backup_loop, daemon=True)
    backup_thread.start()

    # Start group topic maintenance loop
    group_thread = threading.Thread(target=_group_topic_loop, daemon=True)
    group_thread.start()

    # Start worker API server if enabled
    if setting_get("worker_api_enabled", "0") == "1":
        try:
            from api import app as flask_app
            api_port = int(setting_get("worker_api_port", "8080") or "8080")
            api_thread = threading.Thread(
                target=lambda: flask_app.run(host="0.0.0.0", port=api_port, use_reloader=False),
                daemon=True
            )
            api_thread.start()
            print(f"✅ Worker API server started on port {api_port}")
        except Exception as e:
            print(f"⚠️ Could not start API server: {e}")

    # Remove any active webhook before starting long-polling (prevents 409 conflict)
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"⚠️ Could not delete webhook: {e}")

    print("✅ Bot is running...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
