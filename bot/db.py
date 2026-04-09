# -*- coding: utf-8 -*-
import json
import sqlite3
import uuid
from datetime import datetime, timedelta

from .config import DB_NAME, CRYPTO_COINS
from .helpers import now_str


# ── Connection ─────────────────────────────────────────────────────────────────
def get_conn():
    c = sqlite3.connect(DB_NAME, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")  # allow concurrent reads during writes
    return c


# ── Database Initialisation ────────────────────────────────────────────────────
def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                full_name    TEXT,
                username     TEXT,
                balance      INTEGER NOT NULL DEFAULT 0,
                joined_at    TEXT    NOT NULL,
                last_seen_at TEXT    NOT NULL,
                first_start_notified INTEGER NOT NULL DEFAULT 0,
                status       TEXT    NOT NULL DEFAULT 'unsafe',
                is_agent     INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS config_types (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                is_active   INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS packages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id       INTEGER NOT NULL,
                name          TEXT    NOT NULL,
                volume_gb     REAL    NOT NULL,
                duration_days INTEGER NOT NULL,
                price         INTEGER NOT NULL,
                active        INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(type_id) REFERENCES config_types(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS configs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id             INTEGER NOT NULL,
                package_id          INTEGER NOT NULL,
                service_name        TEXT    NOT NULL,
                config_text         TEXT    NOT NULL,
                inquiry_link        TEXT,
                created_at          TEXT    NOT NULL,
                reserved_payment_id INTEGER,
                sold_to             INTEGER,
                purchase_id         INTEGER,
                sold_at             TEXT,
                is_expired          INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(type_id)    REFERENCES config_types(id) ON DELETE CASCADE,
                FOREIGN KEY(package_id) REFERENCES packages(id)     ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                kind            TEXT    NOT NULL,
                user_id         INTEGER NOT NULL,
                package_id      INTEGER,
                amount          INTEGER NOT NULL,
                payment_method  TEXT    NOT NULL,
                status          TEXT    NOT NULL,
                receipt_file_id TEXT,
                receipt_text    TEXT,
                admin_note      TEXT,
                created_at      TEXT    NOT NULL,
                approved_at     TEXT,
                config_id       INTEGER,
                crypto_coin     TEXT
            );
            CREATE TABLE IF NOT EXISTS purchases (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                package_id     INTEGER NOT NULL,
                config_id      INTEGER NOT NULL,
                amount         INTEGER NOT NULL,
                payment_method TEXT    NOT NULL,
                created_at     TEXT    NOT NULL,
                is_test        INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agency_prices (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                package_id INTEGER NOT NULL,
                price      INTEGER NOT NULL,
                UNIQUE(user_id, package_id)
            );
            CREATE TABLE IF NOT EXISTS agency_price_config (
                user_id     INTEGER PRIMARY KEY,
                price_mode  TEXT NOT NULL DEFAULT 'package',
                global_type TEXT NOT NULL DEFAULT 'pct',
                global_val  INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agency_type_discount (
                user_id        INTEGER NOT NULL,
                type_id        INTEGER NOT NULL,
                discount_type  TEXT NOT NULL DEFAULT 'pct',
                discount_value INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, type_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_users (
                user_id     INTEGER PRIMARY KEY,
                added_by    INTEGER NOT NULL,
                added_at    TEXT    NOT NULL,
                permissions TEXT    NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS pending_orders (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                package_id     INTEGER NOT NULL,
                payment_id     INTEGER,
                amount         INTEGER NOT NULL,
                payment_method TEXT    NOT NULL,
                created_at     TEXT    NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'waiting'
            );
            CREATE TABLE IF NOT EXISTS panels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                ip          TEXT    NOT NULL,
                port        INTEGER NOT NULL DEFAULT 2053,
                patch       TEXT    NOT NULL DEFAULT '',
                username    TEXT    NOT NULL,
                password    TEXT    NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS panel_packages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                panel_id      INTEGER NOT NULL,
                name          TEXT    NOT NULL,
                volume_gb     INTEGER NOT NULL,
                duration_days INTEGER NOT NULL,
                inbound_id    INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL,
                FOREIGN KEY(panel_id) REFERENCES panels(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS xui_jobs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_uuid         TEXT    NOT NULL UNIQUE,
                user_id          INTEGER NOT NULL,
                panel_id         INTEGER NOT NULL,
                panel_package_id INTEGER NOT NULL,
                payment_id       INTEGER,
                status           TEXT    NOT NULL DEFAULT 'pending',
                result_config    TEXT,
                result_link      TEXT,
                error_msg        TEXT,
                retry_count      INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL,
                updated_at       TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pinned_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pinned_message_sends (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                pin_id     INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referee_id  INTEGER NOT NULL UNIQUE,
                created_at  TEXT    NOT NULL,
                start_reward_given   INTEGER NOT NULL DEFAULT 0,
                purchase_reward_given INTEGER NOT NULL DEFAULT 0
            );
        """)

        defaults = {
            "support_username": "",
            "payment_card":     "",
            "payment_bank":     "",
            "payment_owner":    "",
            "gw_card_enabled":      "0",
            "gw_card_visibility":   "public",
            "gw_card_display_name": "",
            "gw_crypto_enabled":    "0",
            "gw_crypto_visibility": "public",
            "gw_crypto_display_name": "",
            "gw_tetrapay_enabled":    "0",
            "gw_tetrapay_visibility": "public",
            "gw_tetrapay_display_name": "",
            "tetrapay_api_key":       "",
            "tetrapay_mode_bot":      "1",
            "tetrapay_mode_web":      "1",
            "gw_card_range_enabled":       "0",
            "gw_card_range_min":           "",
            "gw_card_range_max":           "",
            "gw_crypto_range_enabled":     "0",
            "gw_crypto_range_min":         "",
            "gw_crypto_range_max":         "",
            "gw_tetrapay_range_enabled":   "0",
            "gw_tetrapay_range_min":       "",
            "gw_tetrapay_range_max":       "",
            "gw_swapwallet_crypto_enabled":    "0",
            "gw_swapwallet_crypto_visibility": "public",
            "gw_swapwallet_crypto_display_name": "",
            "gw_swapwallet_crypto_range_enabled": "0",
            "gw_swapwallet_crypto_range_min":     "",
            "gw_swapwallet_crypto_range_max":     "",
            "swapwallet_crypto_api_key":  "",
            "swapwallet_crypto_username": "",
            "gw_tronpays_rial_display_name": "",
            "shop_open":         "1",
            "preorder_mode":     "0",
            "support_link":     "",
            "support_link_desc": "",
            "start_text":       "",
            "channel_id":       "",
            "backup_enabled":   "0",
            "backup_interval":  "24",
            "backup_target_id": "",
            "free_test_enabled": "1",
            "agent_test_limit": "0",
            "agent_test_period": "day",
            "purchase_rules_enabled": "0",
            "purchase_rules_text": "♨️ قوانین استفاده از خدمات ما\n\nلطفاً پیش از استفاده از سرویس‌ها، موارد زیر را با دقت مطالعه فرمایید:\n\n1️⃣ اطلاعیه‌های منتشرشده در کانال را حتماً دنبال کنید.\n\n2️⃣ در صورتی که با مشکلی در اتصال مواجه شدید، به پشتیبانی پیام دهید.\n\n3️⃣ از ارسال مشخصات سرویس از طریق پیامک خودداری کنید.\n\n4️⃣ مسئولیت حفظ اطلاعات سرویس بر عهده کاربر می‌باشد.\n\n5️⃣ هرگونه سوءاستفاده ممکن است منجر به مسدود شدن سرویس شود.",
            "worker_api_key":     "",
            "worker_api_port":    "8080",
            "worker_api_enabled": "0",
            "group_id":                    "",
            "group_topic_backup":           "",
            "group_topic_new_users":        "",
            "group_topic_payment_approval": "",
            "group_topic_renewal_request":  "",
            "group_topic_purchase_log":     "",
            "group_topic_renewal_log":      "",
            "group_topic_wallet_log":       "",
            "group_topic_test_report":      "",
            "group_topic_broadcast_report": "",
            "group_topic_error_log":        "",
            "agency_request_enabled":       "1",
            "agency_default_discount_pct":  "20",
            "referral_enabled":             "1",
            "referral_banner_text":         "",
            "referral_banner_photo":        "",
            "referral_start_reward_enabled":  "0",
            "referral_start_reward_count":    "1",
            "referral_start_reward_type":     "wallet",
            "referral_start_reward_amount":   "0",
            "referral_start_reward_package":  "",
            "referral_purchase_reward_enabled": "0",
            "referral_purchase_reward_count":   "1",
            "referral_purchase_reward_type":    "wallet",
            "referral_purchase_reward_amount":  "0",
            "referral_purchase_reward_package": "",
        }
        for coin, _ in CRYPTO_COINS:
            defaults[f"crypto_{coin}"] = ""

        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v)
            )

        migrations = [
            "ALTER TABLE users ADD COLUMN first_start_notified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'safe'",
            "ALTER TABLE users ADD COLUMN is_agent INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE configs ADD COLUMN is_expired INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE payments ADD COLUMN crypto_coin TEXT",
            "ALTER TABLE packages ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE config_types ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE panel_packages ADD COLUMN inbound_id INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE config_types ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
            "CREATE TABLE IF NOT EXISTS pinned_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS pinned_message_sends (id INTEGER PRIMARY KEY AUTOINCREMENT, pin_id INTEGER NOT NULL, user_id INTEGER NOT NULL, message_id INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS referrals (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referee_id INTEGER NOT NULL UNIQUE, created_at TEXT NOT NULL, start_reward_given INTEGER NOT NULL DEFAULT 0, purchase_reward_given INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS agency_request_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, referee_uid INTEGER NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS payment_admin_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id INTEGER NOT NULL, admin_id INTEGER NOT NULL, message_id INTEGER NOT NULL)",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass


# ── Settings ───────────────────────────────────────────────────────────────────
def setting_get(key, default=""):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def setting_set(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )


# ── Users ──────────────────────────────────────────────────────────────────────
def ensure_user(tg_user):
    from .helpers import display_name
    uid       = tg_user.id
    full_name = display_name(tg_user)
    username  = tg_user.username or ""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET full_name=?, username=?, last_seen_at=? WHERE user_id=?",
                (full_name, username, now_str(), uid)
            )
            return False
        conn.execute(
            "INSERT INTO users(user_id,full_name,username,joined_at,last_seen_at,"
            "first_start_notified,status,is_agent) VALUES(?,?,?,?,?,0,'unsafe',0)",
            (uid, full_name, username, now_str(), now_str())
        )
        return True


def get_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def notify_first_start_if_needed(tg_user):
    from .config import ADMIN_IDS
    from .bot_instance import bot
    from .helpers import display_name, display_username, esc
    uid = tg_user.id
    with get_conn() as conn:
        row = conn.execute(
            "SELECT first_start_notified FROM users WHERE user_id=?", (uid,)
        ).fetchone()
        if not row or row["first_start_notified"]:
            return
        conn.execute(
            "UPDATE users SET first_start_notified=1 WHERE user_id=?", (uid,)
        )
    text = (
        "📢 | یه گل جدید عضو ربات شد:\n\n"
        f"نام: {display_name(tg_user)}\n"
        f"نام کاربری: {display_username(tg_user.username)}\n"
        f"آیدی عددی: <code>{tg_user.id}</code>"
    )
    if setting_get("notif_own_new_users", "1") == "1":
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, text)
            except Exception:
                pass
    if setting_get("notif_bot_new_users", "1") == "1":
        import json as _json
        for row in get_all_admin_users():
            sub_id = row["user_id"]
            if sub_id in ADMIN_IDS:
                continue
            perms = _json.loads(row["permissions"] or "{}")
            if not (perms.get("full") or perms.get("approve_payments")):
                continue
            try:
                bot.send_message(sub_id, text)
            except Exception:
                pass
    from .group_manager import send_to_topic as _send_to_topic
    _send_to_topic("new_users", text)


def get_users(has_purchase=None, limit=None, offset=0, status=None):
    q = (
        "SELECT u.*, "
        "(SELECT COUNT(*) FROM purchases p WHERE p.user_id=u.user_id) AS purchase_count "
        "FROM users u WHERE 1=1"
    )
    params = []
    if has_purchase is True:
        q += " AND EXISTS (SELECT 1 FROM purchases p WHERE p.user_id=u.user_id)"
    elif has_purchase is False:
        q += " AND NOT EXISTS (SELECT 1 FROM purchases p WHERE p.user_id=u.user_id)"
    if status is not None:
        q += " AND u.status=?"
        params.append(status)
    q += " ORDER BY u.user_id DESC"
    if limit is not None:
        q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    with get_conn() as conn:
        return conn.execute(q, params).fetchall()


def get_user_detail(user_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.*,
                   (SELECT COUNT(*) FROM purchases p WHERE p.user_id=u.user_id) AS purchase_count,
                   (SELECT COALESCE(SUM(amount),0) FROM purchases p WHERE p.user_id=u.user_id) AS total_spent
            FROM users u WHERE u.user_id=?
            """,
            (user_id,)
        ).fetchone()


def count_all_users():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def count_users_stats():
    """Return (total, with_purchase, new_today)."""
    from .helpers import now_str
    today = now_str()[:10]  # YYYY-MM-DD
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        buyers = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM purchases"
        ).fetchone()["n"]
        new_today = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE joined_at LIKE ?", (f"{today}%",)
        ).fetchone()["n"]
    return total, buyers, new_today


def search_users(query):
    query = query.lstrip("@").strip()
    with get_conn() as conn:
        base = (
            "SELECT u.*, "
            "(SELECT COUNT(*) FROM purchases p WHERE p.user_id=u.user_id) AS purchase_count "
            "FROM users u WHERE "
        )
        if query.isdigit():
            return conn.execute(
                base + "u.user_id=? LIMIT 50", (int(query),)
            ).fetchall()
        return conn.execute(
            base + "(u.full_name LIKE ? OR u.username LIKE ?) "
            "ORDER BY u.user_id DESC LIMIT 50",
            (f"%{query}%", f"%{query}%")
        ).fetchall()


def update_balance(user_id, delta):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?", (delta, user_id)
        )


def set_balance(user_id, amount):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))


def set_user_status(user_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id))


def set_user_agent(user_id, is_agent):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_agent=? WHERE user_id=?", (is_agent, user_id)
        )


# ── Config Types ───────────────────────────────────────────────────────────────
def get_all_types():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM config_types ORDER BY id DESC"
        ).fetchall()


def get_active_types():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM config_types WHERE is_active=1 ORDER BY id DESC"
        ).fetchall()


def get_type(type_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM config_types WHERE id=?", (type_id,)
        ).fetchone()


def add_type(name, description=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config_types(name, description) VALUES(?, ?)",
            (name.strip(), description.strip())
        )


def update_type(type_id, new_name):
    with get_conn() as conn:
        conn.execute(
            "UPDATE config_types SET name=? WHERE id=?", (new_name.strip(), type_id)
        )


def update_type_description(type_id, description):
    with get_conn() as conn:
        conn.execute(
            "UPDATE config_types SET description=? WHERE id=?",
            (description.strip(), type_id)
        )


def update_type_active(type_id, is_active):
    with get_conn() as conn:
        conn.execute(
            "UPDATE config_types SET is_active=? WHERE id=?", (is_active, type_id)
        )


def delete_type(type_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM config_types WHERE id=?", (type_id,))


# ── Packages ───────────────────────────────────────────────────────────────────
def get_packages(type_id=None, price_only=None, include_inactive=False):
    q = """
        SELECT p.*, t.name AS type_name,
        (SELECT COUNT(*) FROM configs c WHERE c.package_id=p.id
         AND c.sold_to IS NULL AND c.reserved_payment_id IS NULL
         AND c.is_expired=0) AS stock
        FROM packages p
        JOIN config_types t ON t.id=p.type_id
        WHERE 1=1
    """
    if not include_inactive:
        q += " AND p.active=1"
    params = []
    if type_id is not None:
        q += " AND p.type_id=?"
        params.append(type_id)
    if price_only is not None:
        q += " AND p.price=?"
        params.append(price_only)
    q += " ORDER BY p.position ASC, p.id ASC"
    with get_conn() as conn:
        return conn.execute(q, params).fetchall()


def get_package(package_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT p.*, t.name AS type_name,
            (SELECT COUNT(*) FROM configs c WHERE c.package_id=p.id
             AND c.sold_to IS NULL AND c.reserved_payment_id IS NULL
             AND c.is_expired=0) AS stock
            FROM packages p
            JOIN config_types t ON t.id=p.type_id
            WHERE p.id=?
            """,
            (package_id,)
        ).fetchone()


def add_package(type_id, name, volume_gb, duration_days, price):
    with get_conn() as conn:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position),0) FROM packages WHERE type_id=?", (type_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO packages(type_id,name,volume_gb,duration_days,price,active,position)"
            " VALUES(?,?,?,?,?,1,?)",
            (type_id, name.strip(), volume_gb, duration_days, price, max_pos + 1)
        )


def toggle_package_active(package_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE packages SET active=((active+1)%2) WHERE id=?", (package_id,)
        )


def update_package_field(package_id, field, value):
    allowed = {"name", "volume_gb", "duration_days", "price", "position"}
    if field not in allowed:
        return
    with get_conn() as conn:
        if field == "position":
            pkg = conn.execute(
                "SELECT type_id, position FROM packages WHERE id=?", (package_id,)
            ).fetchone()
            if pkg:
                old_pos = pkg["position"]
                new_pos = value
                type_id = pkg["type_id"]
                if new_pos != old_pos:
                    if new_pos < old_pos:
                        conn.execute(
                            "UPDATE packages SET position=position+1 "
                            "WHERE type_id=? AND position>=? AND position<? AND id!=?",
                            (type_id, new_pos, old_pos, package_id)
                        )
                    else:
                        conn.execute(
                            "UPDATE packages SET position=position-1 "
                            "WHERE type_id=? AND position>? AND position<=? AND id!=?",
                            (type_id, old_pos, new_pos, package_id)
                        )
                    conn.execute(
                        "UPDATE packages SET position=? WHERE id=?", (new_pos, package_id)
                    )
                    all_pkgs = conn.execute(
                        "SELECT id FROM packages WHERE type_id=? ORDER BY position ASC, id ASC",
                        (type_id,)
                    ).fetchall()
                    for idx, row in enumerate(all_pkgs, 1):
                        conn.execute(
                            "UPDATE packages SET position=? WHERE id=?", (idx, row["id"])
                        )
            return
        conn.execute(f"UPDATE packages SET {field}=? WHERE id=?", (value, package_id))


def delete_package(package_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM packages WHERE id=?", (package_id,))


# ── Configs / Stock ────────────────────────────────────────────────────────────
def add_config(type_id, package_id, service_name, config_text, inquiry_link):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO configs(type_id,package_id,service_name,config_text,"
            "inquiry_link,created_at) VALUES(?,?,?,?,?,?)",
            (type_id, package_id, service_name.strip(),
             config_text.strip(), inquiry_link.strip(), now_str())
        )


def get_registered_packages_stock():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT p.id, p.name, p.volume_gb, p.duration_days, p.price,
                   t.name AS type_name,
                   COUNT(c.id) FILTER (WHERE c.sold_to IS NULL AND c.reserved_payment_id IS NULL
                                       AND c.is_expired=0)          AS stock,
                   COUNT(c.id) FILTER (WHERE c.sold_to IS NOT NULL) AS sold_count,
                   COUNT(c.id) FILTER (WHERE c.is_expired=1)        AS expired_count,
                   (SELECT COUNT(*) FROM pending_orders po
                    WHERE po.package_id=p.id AND po.status='waiting') AS pending_count
            FROM packages p
            JOIN config_types t ON t.id=p.type_id
            LEFT JOIN configs c ON c.package_id=p.id
            WHERE p.active=1
            GROUP BY p.id
            ORDER BY p.id DESC
            """
        ).fetchall()


def get_configs_paginated(package_id, sold, page=0):
    from .config import CONFIGS_PER_PAGE
    offset = page * CONFIGS_PER_PAGE
    if sold:
        q = ("SELECT * FROM configs WHERE package_id=? AND sold_to IS NOT NULL "
             "ORDER BY id DESC LIMIT ? OFFSET ?")
    else:
        q = ("SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL "
             "AND reserved_payment_id IS NULL ORDER BY id ASC LIMIT ? OFFSET ?")
    with get_conn() as conn:
        return conn.execute(q, (package_id, CONFIGS_PER_PAGE, offset)).fetchall()


def count_configs(package_id, sold):
    if sold:
        q = "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL"
    else:
        q = ("SELECT COUNT(*) AS n FROM configs WHERE package_id=? "
             "AND sold_to IS NULL AND reserved_payment_id IS NULL")
    with get_conn() as conn:
        return conn.execute(q, (package_id,)).fetchone()["n"]


def get_available_configs_for_package(package_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL "
            "AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id ASC",
            (package_id,)
        ).fetchall()


def reserve_first_config(package_id, payment_id=None):
    with get_conn() as conn:
        # Always use atomic single-statement UPDATE to prevent race conditions.
        # When no payment_id is supplied (e.g. wallet / free-test), generate a
        # temporary unique key so the UPDATE still locks the row atomically.
        reserve_key = payment_id or f"tmp_{uuid.uuid4().hex}"
        conn.execute(
            "UPDATE configs SET reserved_payment_id=? "
            "WHERE id=("
            "  SELECT id FROM configs "
            "  WHERE package_id=? AND sold_to IS NULL "
            "  AND reserved_payment_id IS NULL AND is_expired=0 "
            "  ORDER BY id ASC LIMIT 1"
            ")",
            (reserve_key, package_id),
        )
        changed = conn.execute("SELECT changes() AS c").fetchone()["c"]
        if changed == 0:
            return None
        row = conn.execute(
            "SELECT id FROM configs WHERE reserved_payment_id=? AND sold_to IS NULL",
            (reserve_key,),
        ).fetchone()
        return row["id"] if row else None


def release_reserved_config(config_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE configs SET reserved_payment_id=NULL WHERE id=?", (config_id,)
        )


def expire_config(config_id):
    with get_conn() as conn:
        conn.execute("UPDATE configs SET is_expired=1 WHERE id=?", (config_id,))


def update_config_field(config_id, field, value):
    """Update a single editable field on a config row."""
    allowed = {"service_name", "config_text", "inquiry_link", "package_id", "type_id"}
    if field not in allowed:
        raise ValueError(f"Field '{field}' is not editable")
    with get_conn() as conn:
        conn.execute(f"UPDATE configs SET {field}=? WHERE id=?", (value, config_id))


def assign_config_to_user(config_id, user_id, package_id, amount, payment_method, is_test=0):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO purchases(user_id,package_id,config_id,amount,"
            "payment_method,created_at,is_test) VALUES(?,?,?,?,?,?,?)",
            (user_id, package_id, config_id, amount, payment_method, now_str(), is_test)
        )
        purchase_id = conn.execute(
            "SELECT last_insert_rowid() AS x"
        ).fetchone()["x"]
        result = conn.execute(
            "UPDATE configs SET sold_to=?, purchase_id=?, sold_at=?, "
            "reserved_payment_id=NULL WHERE id=? AND sold_to IS NULL",
            (user_id, purchase_id, now_str(), config_id)
        )
        changed = conn.execute("SELECT changes() AS c").fetchone()["c"]
        if changed == 0:
            # Config was already sold to someone else (concurrent approval)
            conn.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))
            raise RuntimeError(
                f"Config {config_id} was already assigned to another user "
                "(concurrent payment approval detected)."
            )
        return purchase_id


def get_purchase(purchase_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT pr.*, p.name AS package_name, p.volume_gb, p.duration_days, p.price,
                   t.name AS type_name, t.description AS type_description,
                   c.service_name, c.config_text, c.inquiry_link, c.is_expired
            FROM purchases pr
            JOIN packages p ON p.id=pr.package_id
            JOIN config_types t ON t.id=p.type_id
            JOIN configs c ON c.id=pr.config_id
            WHERE pr.id=?
            """,
            (purchase_id,)
        ).fetchone()


def get_user_purchases(user_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT pr.*, p.name AS package_name, p.volume_gb, p.duration_days, p.price,
                   t.name AS type_name, t.description AS type_description,
                   c.service_name, c.config_text, c.inquiry_link, c.is_expired
            FROM purchases pr
            JOIN packages p ON p.id=pr.package_id
            JOIN config_types t ON t.id=p.type_id
            JOIN configs c ON c.id=pr.config_id
            WHERE pr.user_id=?
            ORDER BY pr.id DESC
            """,
            (user_id,)
        ).fetchall()


def user_has_test_for_type(user_id, type_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM purchases pr "
            "JOIN packages p ON p.id=pr.package_id "
            "WHERE pr.user_id=? AND p.type_id=? AND pr.is_test=1",
            (user_id, type_id)
        ).fetchone()
    return row["n"] > 0


def user_has_any_test(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM purchases WHERE user_id=? AND is_test=1",
            (user_id,)
        ).fetchone()
    return row["n"] > 0


def reset_all_free_tests():
    with get_conn() as conn:
        conn.execute("DELETE FROM purchases WHERE is_test=1")


def agent_test_count_in_period(user_id, period):
    now = datetime.now()
    if period == "day":
        start = now.strftime("%Y-%m-%d 00:00:00")
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d 00:00:00")
    elif period == "month":
        start = now.strftime("%Y-%m-01 00:00:00")
    else:
        start = now.strftime("%Y-%m-%d 00:00:00")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM purchases WHERE user_id=? AND is_test=1 AND created_at>=?",
            (user_id, start)
        ).fetchone()
    return row["cnt"] if row else 0


# ── Agency Prices ──────────────────────────────────────────────────────────────
def get_agency_price(user_id, package_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT price FROM agency_prices WHERE user_id=? AND package_id=?",
            (user_id, package_id)
        ).fetchone()
    return row["price"] if row else None


def set_agency_price(user_id, package_id, price):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agency_prices(user_id,package_id,price) VALUES(?,?,?) "
            "ON CONFLICT(user_id,package_id) DO UPDATE SET price=excluded.price",
            (user_id, package_id, price)
        )


def get_agency_price_config(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agency_price_config WHERE user_id=?", (user_id,)
        ).fetchone()
    if row:
        return dict(row)
    return {"price_mode": "package", "global_type": "pct", "global_val": 0}


def set_agency_price_config(user_id, price_mode, global_type="pct", global_val=0):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agency_price_config(user_id,price_mode,global_type,global_val) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET price_mode=excluded.price_mode,"
            "global_type=excluded.global_type,global_val=excluded.global_val",
            (user_id, price_mode, global_type, global_val)
        )


def get_agency_type_discount(user_id, type_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agency_type_discount WHERE user_id=? AND type_id=?",
            (user_id, type_id)
        ).fetchone()
    return dict(row) if row else None


def set_agency_type_discount(user_id, type_id, discount_type, discount_value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agency_type_discount(user_id,type_id,discount_type,discount_value) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,type_id) DO UPDATE SET discount_type=excluded.discount_type,"
            "discount_value=excluded.discount_value",
            (user_id, type_id, discount_type, discount_value)
        )


def get_agencies():
    """Return all users with is_agent=1."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE is_agent=1 ORDER BY user_id DESC"
        ).fetchall()


# ── Payments ───────────────────────────────────────────────────────────────────
def create_payment(kind, user_id, package_id, amount, payment_method,
                   status="pending", config_id=None, crypto_coin=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO payments(kind,user_id,package_id,amount,payment_method,"
            "status,created_at,config_id,crypto_coin) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, user_id, package_id, amount, payment_method,
             status, now_str(), config_id, crypto_coin)
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]


def get_payment(payment_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE id=?", (payment_id,)
        ).fetchone()


def update_payment_receipt(payment_id, file_id, text_value):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET receipt_file_id=?, receipt_text=? WHERE id=?",
            (file_id, text_value, payment_id)
        )


def approve_payment(payment_id, admin_note):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET status='approved', admin_note=?, approved_at=? WHERE id=?",
            (admin_note, now_str(), payment_id)
        )


def reject_payment(payment_id, admin_note):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET status='rejected', admin_note=?, approved_at=? WHERE id=?",
            (admin_note, now_str(), payment_id)
        )


def complete_payment(payment_id):
    """Mark payment completed. Returns True if this call won the race, False if already processed."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET status='completed', approved_at=? WHERE id=? AND status IN ('pending', 'approved')",
            (now_str(), payment_id)
        )
        changed = conn.execute("SELECT changes() AS c").fetchone()["c"]
        return changed > 0


# ── Admin Users ────────────────────────────────────────────────────────────────
def get_admin_user(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM admin_users WHERE user_id=?", (user_id,)
        ).fetchone()


def get_all_admin_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM admin_users ORDER BY added_at DESC"
        ).fetchall()


def add_admin_user(user_id, added_by, permissions_dict):
    perms_json = json.dumps(permissions_dict, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_users(user_id,added_by,added_at,permissions) VALUES(?,?,?,?)"
            " ON CONFLICT(user_id) DO UPDATE SET permissions=excluded.permissions,"
            " added_by=excluded.added_by, added_at=excluded.added_at",
            (user_id, added_by, now_str(), perms_json)
        )


def remove_admin_user(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_users WHERE user_id=?", (user_id,))


def update_admin_permissions(user_id, permissions_dict):
    perms_json = json.dumps(permissions_dict, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            "UPDATE admin_users SET permissions=? WHERE user_id=?",
            (perms_json, user_id)
        )


# ── 3x-ui Panels ──────────────────────────────────────────────────────────────
def get_all_panels():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM panels ORDER BY id DESC").fetchall()


def get_panel(panel_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM panels WHERE id=?", (panel_id,)
        ).fetchone()


def add_panel(name, ip, port, patch, username, password):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO panels(name,ip,port,patch,username,password,is_active,created_at)"
            " VALUES(?,?,?,?,?,?,1,?)",
            (name.strip(), ip.strip(), int(port), patch.strip(),
             username.strip(), password, now_str())
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]


def update_panel_field(panel_id, field, value):
    allowed = {"name", "ip", "port", "patch", "username", "password", "is_active"}
    if field not in allowed:
        return
    with get_conn() as conn:
        conn.execute(f"UPDATE panels SET {field}=? WHERE id=?", (value, panel_id))


def delete_panel(panel_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM panels WHERE id=?", (panel_id,))


def get_panel_packages(panel_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM panel_packages WHERE panel_id=? ORDER BY id ASC", (panel_id,)
        ).fetchall()


def get_panel_package(pp_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM panel_packages WHERE id=?", (pp_id,)
        ).fetchone()


def add_panel_package(panel_id, name, volume_gb, duration_days, inbound_id=1):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO panel_packages(panel_id,name,volume_gb,duration_days,"
            "inbound_id,created_at) VALUES(?,?,?,?,?,?)",
            (panel_id, name.strip(), int(volume_gb),
             int(duration_days), int(inbound_id), now_str())
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]


def delete_panel_package(pp_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM panel_packages WHERE id=?", (pp_id,))


def create_xui_job(user_id, panel_id, panel_package_id, payment_id=None):
    job_uuid_str = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO xui_jobs(job_uuid,user_id,panel_id,panel_package_id,"
            "payment_id,status,retry_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'pending',0,?,?)",
            (job_uuid_str, user_id, panel_id, panel_package_id,
             payment_id, now_str(), now_str())
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"], job_uuid_str


def get_xui_job(job_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM xui_jobs WHERE id=?", (job_id,)).fetchone()


def get_pending_xui_jobs():
    with get_conn() as conn:
        return conn.execute(
            "SELECT j.*, p.ip, p.port, p.patch, p.username, p.password,"
            " pp.name AS pkg_name, pp.volume_gb, pp.duration_days, pp.inbound_id"
            " FROM xui_jobs j"
            " JOIN panels p ON p.id=j.panel_id"
            " JOIN panel_packages pp ON pp.id=j.panel_package_id"
            " WHERE j.status IN ('pending','failed') AND j.retry_count < 5"
            " ORDER BY j.created_at ASC LIMIT 20"
        ).fetchall()


def update_xui_job(job_id, status, result_config=None, result_link=None, error_msg=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE xui_jobs SET status=?, result_config=?, result_link=?, error_msg=?,"
            " retry_count=retry_count+1, updated_at=? WHERE id=?",
            (status, result_config, result_link, error_msg, now_str(), job_id)
        )


def get_user_xui_jobs(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT j.*, pp.name AS pkg_name, pp.volume_gb, pp.duration_days,"
            " p.name AS panel_name"
            " FROM xui_jobs j"
            " JOIN panel_packages pp ON pp.id=j.panel_package_id"
            " JOIN panels p ON p.id=j.panel_id"
            " WHERE j.user_id=? ORDER BY j.created_at DESC",
            (user_id,)
        ).fetchall()


# ── Pending Orders ─────────────────────────────────────────────────────────────
def create_pending_order(user_id, package_id, payment_id, amount, payment_method):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pending_orders(user_id,package_id,payment_id,amount,"
            "payment_method,created_at,status) VALUES(?,?,?,?,?,?,?)",
            (user_id, package_id, payment_id, amount,
             payment_method, now_str(), "waiting")
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]


def get_pending_order(pending_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_orders WHERE id=?", (pending_id,)
        ).fetchone()


def fulfill_pending_order(pending_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_orders SET status='fulfilled' WHERE id=?", (pending_id,)
        )


def get_waiting_pending_orders_for_package(package_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_orders WHERE package_id=? AND status='waiting' "
            "ORDER BY created_at ASC",
            (package_id,)
        ).fetchall()


#  Pinned Messages 
def get_all_pinned_messages():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pinned_messages ORDER BY id ASC"
        ).fetchall()


def get_pinned_message(pin_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pinned_messages WHERE id=?", (pin_id,)
        ).fetchone()


def add_pinned_message(text):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pinned_messages(text, created_at) VALUES(?, ?)",
            (text, now_str())
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]


def update_pinned_message(pin_id, text):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pinned_messages SET text=? WHERE id=?",
            (text, pin_id)
        )


def delete_pinned_message(pin_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM pinned_messages WHERE id=?", (pin_id,))


def save_pinned_send(pin_id, user_id, message_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pinned_message_sends(pin_id, user_id, message_id) VALUES(?,?,?)",
            (pin_id, user_id, message_id)
        )


def get_pinned_sends(pin_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pinned_message_sends WHERE pin_id=?", (pin_id,)
        ).fetchall()


def delete_pinned_sends(pin_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM pinned_message_sends WHERE pin_id=?", (pin_id,))


# ── Referrals ──────────────────────────────────────────────────────────────────
def add_referral(referrer_id, referee_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO referrals(referrer_id, referee_id, created_at) VALUES(?,?,?)",
            (referrer_id, referee_id, now_str())
        )


def get_referral_by_referee(referee_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM referrals WHERE referee_id=?", (referee_id,)
        ).fetchone()


def get_referral_stats(referrer_id):
    """Return referral stats for a user."""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM referrals WHERE referrer_id=?", (referrer_id,)
        ).fetchone()["n"]
        # Count purchases by referees (only first purchase per referee)
        purchase_count = conn.execute(
            "SELECT COUNT(DISTINCT r.referee_id) AS n "
            "FROM referrals r "
            "JOIN purchases p ON p.user_id = r.referee_id AND p.is_test = 0 "
            "WHERE r.referrer_id=?",
            (referrer_id,)
        ).fetchone()["n"]
        total_purchase_amount = conn.execute(
            "SELECT COALESCE(SUM(sub.first_amount), 0) AS total FROM ("
            "  SELECT MIN(p.id) AS first_id, p.amount AS first_amount "
            "  FROM referrals r "
            "  JOIN purchases p ON p.user_id = r.referee_id AND p.is_test = 0 "
            "  WHERE r.referrer_id=? "
            "  GROUP BY r.referee_id"
            ") sub",
            (referrer_id,)
        ).fetchone()["total"]
        return {
            "total_referrals": total,
            "purchase_count": purchase_count,
            "total_purchase_amount": total_purchase_amount,
        }


def count_referrals(referrer_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM referrals WHERE referrer_id=?", (referrer_id,)
        ).fetchone()["n"]


# ── Agency request message tracking ───────────────────────────────────────────────
def save_agency_request_message(referee_uid, chat_id, message_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agency_request_messages (referee_uid, chat_id, message_id) VALUES (?,?,?)",
            (referee_uid, chat_id, message_id),
        )


def get_agency_request_messages(referee_uid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT chat_id, message_id FROM agency_request_messages WHERE referee_uid=?",
            (referee_uid,),
        ).fetchall()


def delete_agency_request_messages(referee_uid):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM agency_request_messages WHERE referee_uid=?", (referee_uid,)
        )


# ── Payment admin message tracking ────────────────────────────────────────────
def save_payment_admin_message(payment_id, admin_id, message_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO payment_admin_messages (payment_id, admin_id, message_id) VALUES (?,?,?)",
            (payment_id, admin_id, message_id),
        )


def get_payment_admin_messages(payment_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT admin_id, message_id FROM payment_admin_messages WHERE payment_id=?",
            (payment_id,),
        ).fetchall()


def delete_payment_admin_messages(payment_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM payment_admin_messages WHERE payment_id=?", (payment_id,)
        )


def count_referee_first_purchases(referrer_id):
    """Count how many of referrer's referees have made at least one non-test purchase."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(DISTINCT r.referee_id) AS n "
            "FROM referrals r "
            "JOIN purchases p ON p.user_id = r.referee_id AND p.is_test = 0 "
            "WHERE r.referrer_id=? AND r.purchase_reward_given = 0",
            (referrer_id,)
        ).fetchone()["n"]


def mark_start_reward_given(referrer_id, referee_ids):
    with get_conn() as conn:
        for rid in referee_ids:
            conn.execute(
                "UPDATE referrals SET start_reward_given=1 WHERE referrer_id=? AND referee_id=?",
                (referrer_id, rid)
            )


def mark_purchase_reward_given(referrer_id, referee_ids):
    with get_conn() as conn:
        for rid in referee_ids:
            conn.execute(
                "UPDATE referrals SET purchase_reward_given=1 WHERE referrer_id=? AND referee_id=?",
                (referrer_id, rid)
            )


def get_unrewarded_start_referrals(referrer_id):
    """Get referees who haven't been rewarded for starting yet."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM referrals WHERE referrer_id=? AND start_reward_given=0",
            (referrer_id,)
        ).fetchall()


def get_unrewarded_purchase_referees(referrer_id):
    """Get referee IDs who made first purchase but referrer hasn't been rewarded."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT DISTINCT r.referee_id FROM referrals r "
            "JOIN purchases p ON p.user_id = r.referee_id AND p.is_test = 0 "
            "WHERE r.referrer_id=? AND r.purchase_reward_given=0",
            (referrer_id,)
        ).fetchall()
