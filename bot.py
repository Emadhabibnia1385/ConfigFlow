# -*- coding: utf-8 -*-
"""
ConfigFlow Telegram Bot  – v4
Requirements:
    pip install pyTelegramBotAPI qrcode pillow python-dotenv
Run:
    export BOT_TOKEN="..."
    export ADMIN_IDS="123456789"
    python bot.py
"""

import io
import os
import re
import html
import json
import sqlite3
import traceback
import threading
import urllib.request
import urllib.parse
from datetime import datetime

from dotenv import load_dotenv
import qrcode
import telebot
from telebot import types

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS  = {int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}
DB_NAME    = os.getenv("DB_NAME", "configflow.db")

BRAND_TITLE          = "ConfigFlow"
DEFAULT_ADMIN_HANDLE = ""
CRYPTO_PRICES_API    = "https://swapwallet.app/api/v1/market/prices"
TETRAPAY_CREATE_URL  = "https://tetra98.com/api/create_order"
TETRAPAY_VERIFY_URL  = "https://tetra98.com/api/verify"
SWAPWALLET_BASE_URL  = "https://swapwallet.app/api"

# ── Admin permission system ────────────────────────────────────────────────────
ADMIN_PERMS = [
    ("full",           "🌟 دسترسی کامل (مانند اونر)"),
    ("types_packages", "🧩 مدیریت نوع و پکیج‌ها"),
    ("register_config","📝 ثبت کانفیگ"),
    ("view_configs",   "👁 دیدن کانفیگ‌های ثبت‌شده"),
    ("manage_configs", "🗑 حذف و منقضی‌کردن کانفیگ‌ها"),
    ("broadcast_all",  "📣 فوروارد همگانی"),
    ("broadcast_cust", "🛍 فوروارد برای مشتریان"),
    ("view_users",     "👥 مدیریت کاربران (فقط مشاهده)"),
    ("agency",         "🤝 تایید/رد نمایندگی"),
    ("assign_config",  "📦 ثبت کانفیگ برای کاربران"),
    ("manage_balance", "💰 مدیریت موجودی کاربران"),
    ("user_status",    "🔐 تعیین امن/ناامن کاربران"),
    ("full_users",       "👑 دسترسی کامل مدیریت کاربران"),
    ("settings",         "⚙️ دسترسی به تنظیمات ربات"),
    ("approve_payments", "💳 تایید یا رد پرداخت‌ها"),
    ("approve_renewal",  "🔄 تایید تمدید کردن"),
]
PERM_FULL_SET  = {"types_packages","register_config","view_configs","manage_configs",
                  "broadcast_all","broadcast_cust","view_users","agency","assign_config",
                  "manage_balance","user_status","full_users","settings",
                  "approve_payments","approve_renewal"}
PERM_USER_FULL = {"agency", "assign_config", "manage_balance", "user_status"}

CRYPTO_API_SYMBOLS = {
    "tron":       "TRX",
    "ton":        "TON",
    "usdt_bep20": "USDT",
    "usdc_bep20": "USDC",
    "ltc":        "LTC",
}

CRYPTO_COINS = [
    ("tron",       "🔵 Tron (TRC20)"),
    ("ton",        "💎 TON"),
    ("usdt_bep20", "🟢 USDT (BEP20)"),
    ("usdc_bep20", "🔵 USDC (BEP20)"),
    ("ltc",        "🪙 LTC"),
]

CONFIGS_PER_PAGE = 10

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise SystemExit("BOT_TOKEN تنظیم نشده یا معتبر نیست.")
if not ADMIN_IDS:
    raise SystemExit("ADMIN_IDS تنظیم نشده است.")

bot        = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
USER_STATE = {}
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩","01234567890123456789")

# ── Helpers ────────────────────────────────────────────────────────────────────
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def is_admin(uid):
    if uid in ADMIN_IDS:
        return True
    # Also check DB-registered sub-admins (requires DB to be initialised first)
    try:
        return get_admin_user(uid) is not None
    except Exception:
        return False

def admin_has_perm(uid, perm):
    """Return True for owners unconditionally; for sub-admins check their JSON permissions."""
    if uid in ADMIN_IDS:
        return True
    try:
        row = get_admin_user(uid)
    except Exception:
        return False
    if not row:
        return False
    perms = json.loads(row["permissions"] or "{}")
    if perms.get("full"):
        return True
    # full_users grants all individual user-management perms
    if perm in PERM_USER_FULL and perms.get("full_users"):
        return True
    return bool(perms.get(perm, False))

def normalize_text_number(v):
    v = (v or "").translate(PERSIAN_DIGITS)
    v = v.replace(",","").replace("٬","").replace(" ","")
    v = v.replace("تومان","").replace("ریال","")
    return v.strip()

def parse_int(v):
    c = normalize_text_number(v)
    if not c or not re.fullmatch(r"\d+", c):
        return None
    return int(c)

def fmt_price(a):
    return f"{int(a):,}"

def display_name(u):
    n = " ".join(p for p in [u.first_name or "", u.last_name or ""] if p).strip()
    return n or "ㅤ"

def display_username(u):
    return f"@{u}" if u else "@ ندارد"

def safe_support_url(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    raw = raw.replace("https://","").replace("http://","")
    raw = raw.replace("t.me/","").replace("telegram.me/","").replace("@","").strip()
    return f"https://t.me/{raw}" if raw else None

# State helpers
def state_set(uid, name, **data):
    USER_STATE[uid] = {"state_name": name, "data": data}

def state_clear(uid):
    USER_STATE.pop(uid, None)

def state_name(uid):
    s = USER_STATE.get(uid)
    return s["state_name"] if s else None

def state_data(uid):
    s = USER_STATE.get(uid)
    return s["data"] if s else {}

def esc(t):
    return html.escape(str(t or ""))

def back_button(target="main"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:{target}"))
    return kb

# ── Database ───────────────────────────────────────────────────────────────────
def get_conn():
    c = sqlite3.connect(DB_NAME, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

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
                description TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS packages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id       INTEGER NOT NULL,
                name          TEXT    NOT NULL,
                volume_gb     INTEGER NOT NULL,
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
        """)

        defaults = {
            "support_username": "",
            "payment_card":     "",
            "payment_bank":     "",
            "payment_owner":    "",
            "gw_card_enabled":      "0",
            "gw_card_visibility":   "public",
            "gw_crypto_enabled":    "0",
            "gw_crypto_visibility": "public",
            "gw_tetrapay_enabled":    "0",
            "gw_tetrapay_visibility": "public",
            "tetrapay_api_key":       "",
            "tetrapay_mode_bot":      "1",
            "tetrapay_mode_web":      "1",
            "gw_swapwallet_enabled":    "0",
            "gw_swapwallet_visibility": "public",
            "swapwallet_api_key":       "",
            "swapwallet_username":      "",
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
            "purchase_rules_text": "♨️ قوانین استفاده از خدمات ما\n\nلطفاً پیش از استفاده از سرویس‌ها، موارد زیر را با دقت مطالعه فرمایید:\n\n1️⃣ اطلاعیه‌های منتشرشده در کانال را حتماً دنبال کنید. هرگونه تغییر، بروزرسانی یا قطعی احتمالی از طریق کانال اطلاع‌رسانی خواهد شد.\n\n2️⃣ در صورتی که با مشکلی در اتصال یا عملکرد سرویس مواجه شدید و اطلاعیه‌ای در کانال منتشر نشده بود، لطفاً به پشتیبانی پیام دهید تا در سریع‌ترین زمان ممکن بررسی شود.\n\n3️⃣ از ارسال مشخصات سرویس (کانفیگ) از طریق پیامک خودداری کنید، زیرا ممکن است به درستی منتقل نشود. در صورت نیاز، از روش‌های امن‌تر مانند ایمیل استفاده نمایید.\n\n4️⃣ مسئولیت حفظ و نگهداری اطلاعات سرویس بر عهده کاربر می‌باشد. از اشتراک‌گذاری آن با دیگران خودداری کنید.\n\n5️⃣ هرگونه سوءاستفاده از خدمات، ممکن است منجر به مسدود شدن سرویس بدون اطلاع قبلی شود.\n\n🙏 با رعایت این قوانین، به ما در ارائه خدمات پایدار و بهتر کمک کنید.",
        }
        for coin, _ in CRYPTO_COINS:
            defaults[f"crypto_{coin}"] = ""

        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))

        migrations = [
            "ALTER TABLE users ADD COLUMN first_start_notified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'safe'",
            "ALTER TABLE users ADD COLUMN is_agent INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE configs ADD COLUMN is_expired INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE payments ADD COLUMN crypto_coin TEXT",
            "ALTER TABLE packages ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE config_types ADD COLUMN description TEXT NOT NULL DEFAULT ''",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass

def setting_get(key, default=""):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def setting_set(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )

# ── Gateway helpers ────────────────────────────────────────────────────────────
def is_gateway_available(gw_name, user_id):
    enabled = setting_get(f"gw_{gw_name}_enabled", "0")
    if enabled != "1":
        return False
    visibility = setting_get(f"gw_{gw_name}_visibility", "public")
    if visibility == "secure":
        user = get_user(user_id)
        return user and user["status"] == "safe"
    return True

def is_card_info_complete():
    return all([
        setting_get("payment_card", ""),
        setting_get("payment_bank", ""),
        setting_get("payment_owner", ""),
    ])

def fetch_crypto_prices():
    try:
        req = urllib.request.Request(CRYPTO_PRICES_API, headers={"User-Agent": "ConfigFlow/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        prices = {}
        for key, val in data.items():
            if key.endswith("/IRT"):
                symbol = key.split("/")[0]
                try:
                    prices[symbol] = float(str(val).replace(",", ""))
                except (ValueError, TypeError):
                    pass
        return prices
    except Exception:
        return {}

def create_tetrapay_order(amount_toman, hash_id, description="پرداخت"):
    api_key = setting_get("tetrapay_api_key", "")
    if not api_key:
        return False, {"error": "API key not set"}
    amount_rial = amount_toman * 10
    payload = json.dumps({
        "ApiKey": api_key,
        "Hash_id": hash_id,
        "Amount": amount_rial,
        "Description": description,
        "Email": "",
        "Mobile": "",
        "CallbackURL": "https://configflow.local/cb"
    }).encode()
    req = urllib.request.Request(
        TETRAPAY_CREATE_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ConfigFlow/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        if str(result.get("status")) == "100":
            return True, result
        return False, result
    except Exception as e:
        return False, {"error": str(e)}

def verify_tetrapay_order(authority):
    api_key = setting_get("tetrapay_api_key", "")
    if not api_key:
        return False, {"error": "API key not set"}
    payload = json.dumps({
        "authority": authority,
        "ApiKey": api_key
    }).encode()
    req = urllib.request.Request(
        TETRAPAY_VERIFY_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ConfigFlow/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        if str(result.get("status")) == "100":
            return True, result
        return False, result
    except Exception as e:
        return False, {"error": str(e)}

def create_swapwallet_invoice(amount_toman, order_id, description="پرداخت"):
    api_key  = setting_get("swapwallet_api_key", "")
    username = setting_get("swapwallet_username", "").strip()
    if not api_key or not username:
        return False, {"error": "SwapWallet credentials not set"}
    payload = json.dumps({
        "amount":       {"number": str(amount_toman), "unit": "IRT"},
        "network":      "TRON",
        "allowedToken": "USDT",
        "ttl":          3600,
        "orderId":      order_id,
        "description":  description,
    }).encode()
    safe_user = urllib.parse.quote(username, safe="")
    url = f"{SWAPWALLET_BASE_URL}/v2/payment/{safe_user}/invoices/temporary-wallet"
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent":    "ConfigFlow/1.0",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        if result.get("status") == "OK":
            return True, result.get("result", {})
        return False, result
    except Exception as e:
        return False, {"error": str(e)}

def check_swapwallet_invoice(invoice_id):
    api_key  = setting_get("swapwallet_api_key", "")
    username = setting_get("swapwallet_username", "").strip()
    if not api_key or not username:
        return False, {"error": "SwapWallet credentials not set"}
    safe_user = urllib.parse.quote(username, safe="")
    safe_inv  = urllib.parse.quote(invoice_id, safe="")
    url = f"{SWAPWALLET_BASE_URL}/v2/payment/{safe_user}/invoices/{safe_inv}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent":    "ConfigFlow/1.0",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        if result.get("status") == "OK":
            return True, result.get("result", {})
        return False, result
    except Exception as e:
        return False, {"error": str(e)}

def ensure_user(tg_user):
    uid = tg_user.id
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
            "INSERT INTO users(user_id,full_name,username,joined_at,last_seen_at,first_start_notified,status,is_agent)"
            " VALUES(?,?,?,?,?,0,'unsafe',0)",
            (uid, full_name, username, now_str(), now_str())
        )
        return True

def get_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def notify_first_start_if_needed(tg_user):
    uid = tg_user.id
    with get_conn() as conn:
        row = conn.execute("SELECT first_start_notified FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row or row["first_start_notified"]:
            return
        conn.execute("UPDATE users SET first_start_notified=1 WHERE user_id=?", (uid,))
    text = (
        "📢 | یه گل جدید عضو ربات شد:\n\n"
        f"نام: {display_name(tg_user)}\n"
        f"نام کاربری: {display_username(tg_user.username)}\n"
        f"آیدی عددی: <code>{tg_user.id}</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text)
        except Exception:
            pass

def get_all_types():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM config_types ORDER BY id DESC").fetchall()

def get_type(type_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM config_types WHERE id=?", (type_id,)).fetchone()

def add_type(name, description=""):
    with get_conn() as conn:
        conn.execute("INSERT INTO config_types(name, description) VALUES(?, ?)", (name.strip(), description.strip()))

def update_type(type_id, new_name):
    with get_conn() as conn:
        conn.execute("UPDATE config_types SET name=? WHERE id=?", (new_name.strip(), type_id))

def update_type_description(type_id, description):
    with get_conn() as conn:
        conn.execute("UPDATE config_types SET description=? WHERE id=?", (description.strip(), type_id))

def delete_type(type_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM config_types WHERE id=?", (type_id,))

def get_packages(type_id=None, price_only=None, include_inactive=False):
    q = """
        SELECT p.*, t.name AS type_name,
        (SELECT COUNT(*) FROM configs c WHERE c.package_id=p.id AND c.sold_to IS NULL AND c.reserved_payment_id IS NULL AND c.is_expired=0) AS stock
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
            (SELECT COUNT(*) FROM configs c WHERE c.package_id=p.id AND c.sold_to IS NULL AND c.reserved_payment_id IS NULL AND c.is_expired=0) AS stock
            FROM packages p
            JOIN config_types t ON t.id=p.type_id
            WHERE p.id=?
            """,
            (package_id,)
        ).fetchone()

def add_package(type_id, name, volume_gb, duration_days, price):
    with get_conn() as conn:
        max_pos = conn.execute("SELECT COALESCE(MAX(position),0) FROM packages WHERE type_id=?", (type_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO packages(type_id,name,volume_gb,duration_days,price,active,position) VALUES(?,?,?,?,?,1,?)",
            (type_id, name.strip(), volume_gb, duration_days, price, max_pos + 1)
        )

def update_package_field(package_id, field, value):
    allowed = {"name", "volume_gb", "duration_days", "price", "position"}
    if field not in allowed:
        return
    with get_conn() as conn:
        if field == "position":
            pkg = conn.execute("SELECT type_id, position FROM packages WHERE id=?", (package_id,)).fetchone()
            if pkg:
                old_pos = pkg["position"]
                new_pos = value
                type_id = pkg["type_id"]
                if new_pos != old_pos:
                    if new_pos < old_pos:
                        # Moving up: shift items in [new_pos, old_pos-1] down by 1
                        conn.execute(
                            "UPDATE packages SET position=position+1 WHERE type_id=? AND position>=? AND position<? AND id!=?",
                            (type_id, new_pos, old_pos, package_id)
                        )
                    else:
                        # Moving down: shift items in [old_pos+1, new_pos] up by 1
                        conn.execute(
                            "UPDATE packages SET position=position-1 WHERE type_id=? AND position>? AND position<=? AND id!=?",
                            (type_id, old_pos, new_pos, package_id)
                        )
                    conn.execute("UPDATE packages SET position=? WHERE id=?", (new_pos, package_id))
                    # Re-normalize positions to be sequential 1,2,3...
                    all_pkgs = conn.execute(
                        "SELECT id FROM packages WHERE type_id=? ORDER BY position ASC, id ASC",
                        (type_id,)
                    ).fetchall()
                    for idx, row in enumerate(all_pkgs, 1):
                        conn.execute("UPDATE packages SET position=? WHERE id=?", (idx, row["id"]))
            return
        conn.execute(f"UPDATE packages SET {field}=? WHERE id=?", (value, package_id))

def delete_package(package_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM packages WHERE id=?", (package_id,))

def add_config(type_id, package_id, service_name, config_text, inquiry_link):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO configs(type_id,package_id,service_name,config_text,inquiry_link,created_at) VALUES(?,?,?,?,?,?)",
            (type_id, package_id, service_name.strip(), config_text.strip(), inquiry_link.strip(), now_str())
        )

def get_registered_packages_stock():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT p.id, p.name, p.volume_gb, p.duration_days, p.price, t.name AS type_name,
                   COUNT(c.id) FILTER (WHERE c.sold_to IS NULL AND c.reserved_payment_id IS NULL AND c.is_expired=0) AS stock,
                   COUNT(c.id) FILTER (WHERE c.sold_to IS NOT NULL) AS sold_count,
                   COUNT(c.id) FILTER (WHERE c.is_expired=1) AS expired_count
            FROM packages p
            JOIN config_types t ON t.id=p.type_id
            LEFT JOIN configs c ON c.package_id=p.id
            WHERE p.active=1
            GROUP BY p.id
            ORDER BY p.id DESC
            """
        ).fetchall()

def get_configs_paginated(package_id, sold, page=0):
    offset = page * CONFIGS_PER_PAGE
    if sold:
        q = "SELECT * FROM configs WHERE package_id=? AND sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?"
    else:
        q = "SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL ORDER BY id ASC LIMIT ? OFFSET ?"
    with get_conn() as conn:
        rows = conn.execute(q, (package_id, CONFIGS_PER_PAGE, offset)).fetchall()
    return rows

def count_configs(package_id, sold):
    if sold:
        q = "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL"
    else:
        q = "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL"
    with get_conn() as conn:
        return conn.execute(q, (package_id,)).fetchone()["n"]

def get_available_configs_for_package(package_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id ASC",
            (package_id,)
        ).fetchall()

def reserve_first_config(package_id, payment_id=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id ASC LIMIT 1",
            (package_id,)
        ).fetchone()
        if not row:
            return None
        if payment_id:
            conn.execute("UPDATE configs SET reserved_payment_id=? WHERE id=?", (payment_id, row["id"]))
        return row["id"]

def release_reserved_config(config_id):
    with get_conn() as conn:
        conn.execute("UPDATE configs SET reserved_payment_id=NULL WHERE id=?", (config_id,))

def expire_config(config_id):
    with get_conn() as conn:
        conn.execute("UPDATE configs SET is_expired=1 WHERE id=?", (config_id,))

def assign_config_to_user(config_id, user_id, package_id, amount, payment_method, is_test=0):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO purchases(user_id,package_id,config_id,amount,payment_method,created_at,is_test) VALUES(?,?,?,?,?,?,?)",
            (user_id, package_id, config_id, amount, payment_method, now_str(), is_test)
        )
        purchase_id = conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]
        conn.execute(
            "UPDATE configs SET sold_to=?, purchase_id=?, sold_at=?, reserved_payment_id=NULL WHERE id=?",
            (user_id, purchase_id, now_str(), config_id)
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
            """
            SELECT 1 FROM purchases pr
            JOIN packages p ON p.id=pr.package_id
            WHERE pr.user_id=? AND pr.is_test=1 AND p.type_id=? LIMIT 1
            """,
            (user_id, type_id)
        ).fetchone()
    return bool(row)

def user_has_any_test(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND is_test=1 LIMIT 1",
            (user_id,)
        ).fetchone()
    return bool(row)

def reset_all_free_tests():
    with get_conn() as conn:
        conn.execute("DELETE FROM purchases WHERE is_test=1")

def agent_test_count_in_period(user_id, period):
    import time
    now = datetime.now()
    if period == "day":
        start = now.strftime("%Y-%m-%d 00:00:00")
    elif period == "week":
        from datetime import timedelta
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

def update_balance(user_id, delta):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, user_id))

def set_balance(user_id, amount):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))

def set_user_status(user_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id))

def set_user_agent(user_id, is_agent):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_agent=? WHERE user_id=?", (is_agent, user_id))

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
            "INSERT INTO agency_prices(user_id,package_id,price) VALUES(?,?,?) ON CONFLICT(user_id,package_id) DO UPDATE SET price=excluded.price",
            (user_id, package_id, price)
        )

def create_payment(kind, user_id, package_id, amount, payment_method, status="pending", config_id=None, crypto_coin=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO payments(kind,user_id,package_id,amount,payment_method,status,created_at,config_id,crypto_coin) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, user_id, package_id, amount, payment_method, status, now_str(), config_id, crypto_coin)
        )
        return conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]

def get_payment(payment_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()

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
    with get_conn() as conn:
        conn.execute("UPDATE payments SET status='completed', approved_at=? WHERE id=?", (now_str(), payment_id))

def get_users(has_purchase=None):
    q = """
        SELECT u.*,
               (SELECT COUNT(*) FROM purchases p WHERE p.user_id=u.user_id) AS purchase_count
        FROM users u WHERE 1=1
    """
    if has_purchase is True:
        q += " AND EXISTS (SELECT 1 FROM purchases p WHERE p.user_id=u.user_id)"
    elif has_purchase is False:
        q += " AND NOT EXISTS (SELECT 1 FROM purchases p WHERE p.user_id=u.user_id)"
    q += " ORDER BY u.user_id DESC"
    with get_conn() as conn:
        return conn.execute(q).fetchall()

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

def search_users(query):
    with get_conn() as conn:
        if query.isdigit():
            rows = conn.execute(
                "SELECT * FROM users WHERE user_id=? LIMIT 50",
                (int(query),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users WHERE full_name LIKE ? OR username LIKE ? ORDER BY user_id DESC LIMIT 50",
                (f"%{query}%", f"%{query}%")
            ).fetchall()
    return rows

# ── Admin user management helpers ─────────────────────────────────────────────
def get_admin_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admin_users WHERE user_id=?", (user_id,)).fetchone()

def get_all_admin_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admin_users ORDER BY added_at DESC").fetchall()

def add_admin_user(user_id, added_by, permissions_dict):
    perms_json = json.dumps(permissions_dict, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_users(user_id, added_by, added_at, permissions) VALUES(?,?,?,?)"
            " ON CONFLICT(user_id) DO UPDATE SET permissions=excluded.permissions, added_by=excluded.added_by, added_at=excluded.added_at",
            (user_id, added_by, now_str(), perms_json)
        )

def remove_admin_user(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_users WHERE user_id=?", (user_id,))

def update_admin_permissions(user_id, permissions_dict):
    perms_json = json.dumps(permissions_dict, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("UPDATE admin_users SET permissions=? WHERE user_id=?", (perms_json, user_id))

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
        # Numeric supergroup/channel ID: use t.me/c/xxx format
        channel_url = f"https://t.me/c/{channel_id[4:]}"
    else:
        channel_url = f"https://t.me/{channel_id}"
    kb.add(types.InlineKeyboardButton("📢 عضویت در کانال", url=channel_url))
    kb.add(types.InlineKeyboardButton("✅ عضو شدم", callback_data="check_channel"))
    send_or_edit(target,
        f"🔒 برای استفاده از ربات، ابتدا باید در کانال ما عضو شوید.\n\nپس از عضویت، روی «عضو شدم» بزنید.",
        kb
    )

# ── Telegram UI helpers ────────────────────────────────────────────────────────
def set_bot_commands():
    try:
        bot.set_my_commands([types.BotCommand("start", "شروع ربات")])
    except Exception:
        pass

def send_or_edit(call_or_msg, text, reply_markup=None, disable_preview=True):
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
            chat_id = call_or_msg.message.chat.id if hasattr(call_or_msg, "message") else call_or_msg.chat.id
            bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=disable_preview)
        except Exception:
            pass

def kb_main(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("🛒 خرید کانفیگ جدید", callback_data="buy:start"),
        types.InlineKeyboardButton("📦 کانفیگ‌های من",    callback_data="my_configs"),
    )
    if setting_get("free_test_enabled", "1") == "1":
        kb.add(types.InlineKeyboardButton("🎁 تست رایگان", callback_data="test:start"))
    kb.row(
        types.InlineKeyboardButton("👤 حساب کاربری",    callback_data="profile"),
        types.InlineKeyboardButton("💳 شارژ کیف پول",   callback_data="wallet:charge"),
    )
    kb.add(types.InlineKeyboardButton("🎧 ارتباط با پشتیبانی", callback_data="support"))
    kb.add(types.InlineKeyboardButton("🤝 درخواست نمایندگی", callback_data="agency:request"))
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("⚙️ ورود به پنل مدیریت", callback_data="admin:panel"))
    return kb

def kb_admin_panel(uid=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    is_owner = (uid in ADMIN_IDS) if uid else False

    # Types & packages
    if is_owner or (uid and admin_has_perm(uid, "types_packages")):
        kb.row(types.InlineKeyboardButton("🧩 مدیریت نوع و پکیج‌ها", callback_data="admin:types"))

    # Configs
    if is_owner or (uid and (admin_has_perm(uid, "view_configs") or
                             admin_has_perm(uid, "register_config") or
                             admin_has_perm(uid, "manage_configs"))):
        kb.row(types.InlineKeyboardButton("📚 کانفیگ‌ها", callback_data="admin:stock"))

    # Users + Admin management
    show_users = is_owner or (uid and (admin_has_perm(uid, "view_users") or
                                       admin_has_perm(uid, "full_users") or
                                       any(admin_has_perm(uid, p) for p in PERM_USER_FULL)))
    if show_users and is_owner:
        kb.row(
            types.InlineKeyboardButton("👥 مدیریت کاربران",  callback_data="admin:users"),
            types.InlineKeyboardButton("👮 مدیریت ادمین‌ها", callback_data="admin:admins"),
        )
    elif show_users:
        kb.row(types.InlineKeyboardButton("👥 مدیریت کاربران", callback_data="admin:users"))
    elif is_owner:
        kb.row(types.InlineKeyboardButton("👮 مدیریت ادمین‌ها", callback_data="admin:admins"))

    # Settings
    if is_owner or (uid and admin_has_perm(uid, "settings")):
        kb.add(types.InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin:settings"))

    # Broadcast (moved below settings)
    if is_owner or (uid and (admin_has_perm(uid, "broadcast_all") or admin_has_perm(uid, "broadcast_cust"))):
        kb.add(types.InlineKeyboardButton("📣 فوروارد همگانی", callback_data="admin:broadcast"))

    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    return kb

def show_main_menu(target):
    uid = target.from_user.id if hasattr(target, "from_user") else target.chat.id
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
    kb.row(
        types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="wallet:charge"),
        types.InlineKeyboardButton("📦 کانفیگ‌های من", callback_data="my_configs"),
    )
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, text, kb)

def show_support(target):
    support_raw = setting_get("support_username", DEFAULT_ADMIN_HANDLE)
    support_url = safe_support_url(support_raw)
    support_link = setting_get("support_link", "")
    support_link_desc = setting_get("support_link_desc", "")

    kb = types.InlineKeyboardMarkup()
    has_any = False
    if support_url:
        kb.add(types.InlineKeyboardButton("💬 پشتیبانی تلگرام", url=support_url))
        has_any = True
    if support_link:
        btn_text = "🌐 پشتیبانی آنلاین"
        kb.add(types.InlineKeyboardButton(btn_text, url=support_link))
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
        title = f"{item['service_name']}{expired_mark}"
        kb.add(types.InlineKeyboardButton(title, callback_data=f"mycfg:{item['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, "📦 <b>کانفیگ‌های من</b>\n\nیکی از سرویس‌ها را انتخاب کنید:", kb)

def deliver_purchase_message(chat_id, purchase_id):
    item = get_purchase(purchase_id)
    if not item:
        bot.send_message(chat_id, "❌ اطلاعات خرید یافت نشد.")
        return
    cfg = item["config_text"]
    expired_note = "\n\n⚠️ <b>این سرویس توسط ادمین منقضی شده است.</b>" if item["is_expired"] else ""
    text = (
        f"✅ <b>{'تست رایگان' if item['is_test'] else 'سرویس شما آماده است'}</b>\n\n"
        f"🔮 نام سرویس: <b>{esc(item['service_name'])}</b>\n"
        f"🧩 نوع سرویس: <b>{esc(item['type_name'])}</b>\n"
        f"🔋 حجم: <b>{item['volume_gb']}</b> گیگ\n"
        f"⏰ مدت: <b>{item['duration_days']}</b> روز\n\n"
        f"💝 <b>Config:</b>\n<code>{esc(cfg)}</code>\n\n"
        f"🔋 Volume web: {esc(item['inquiry_link'] or '-')}"
        f"{expired_note}"
    )
    qr_img = qrcode.make(cfg)
    bio = io.BytesIO()
    qr_img.save(bio, format="PNG")
    bio.seek(0)
    bio.name = "qrcode.png"

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("♻️ تمدید", callback_data=f"renew:{purchase_id}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    bot.send_photo(chat_id, bio, caption=text, reply_markup=kb)

    # Send type description as second message if available
    type_desc = item.get("type_description", "")
    if type_desc:
        bot.send_message(chat_id, f"📌 <b>توضیحات سرویس:</b>\n\n{esc(type_desc)}")

def admin_purchase_notify(method_label, user_row, package_row):
    text = (
        f"❗️ | خرید جدید ({method_label})\n\n"
        f"▫️ آیدی کاربر: <code>{user_row['user_id']}</code>\n"
        f"👨‍💼 نام: {esc(user_row['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(user_row['username'] or 'ندارد')}\n"
        f"💰 مبلغ: {fmt_price(package_row['price'])} تومان\n"
        f"🚦 سرور: {esc(package_row['type_name'])}\n"
        f"✏️ پکیج: {esc(package_row['name'])}\n"
        f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
        f"⏰ مدت: {package_row['duration_days']} روز"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text)
        except Exception:
            pass

def admin_renewal_notify(user_id, purchase_item, package_row, amount, method_label):
    user_row = get_user(user_id)
    config_id = purchase_item["config_id"]
    text = (
        f"♻️ | <b>درخواست تمدید</b> ({method_label})\n\n"
        f"👤 کاربر: {esc(user_row['full_name'])}\n"
        f"⚡️ نام کاربری: {esc(user_row['username'] or 'ندارد')}\n"
        f"🆔 آیدی: <code>{user_row['user_id']}</code>\n"
        f"💰 مبلغ پرداختی: <b>{fmt_price(amount)}</b> تومان\n\n"
        f"📌 <b>سرویس فعلی:</b>\n"
        f"🔮 نام: {esc(purchase_item['service_name'])}\n"
        f"🧩 نوع: {esc(purchase_item['type_name'])}\n\n"
        f"📦 <b>پکیج تمدید:</b>\n"
        f"✏️ نام: {esc(package_row['name'])}\n"
        f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
        f"⏰ مدت: {package_row['duration_days']} روز"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ تمدید انجام شد", callback_data=f"renew:confirm:{config_id}:{user_id}"))
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            pass

# ── Payment helpers ────────────────────────────────────────────────────────────
def get_effective_price(user_id, package_row):
    """Return agency price if user is agent and has a custom price, else regular price."""
    user = get_user(user_id)
    if user and user["is_agent"]:
        ap = get_agency_price(user_id, package_row["id"])
        if ap is not None:
            return ap
    return package_row["price"]

def show_payment_method_selection(target, uid, context_data):
    """
    context_data must contain:
      'kind': 'wallet_charge' or 'config_purchase'
      'amount': int
      optionally 'package_id': int
    """
    amount     = context_data["amount"]
    kind       = context_data["kind"]

    kb = types.InlineKeyboardMarkup()
    if is_gateway_available("card", uid) and is_card_info_complete():
        kb.add(types.InlineKeyboardButton("💳 کارت به کارت", callback_data="pm:card"))
    if is_gateway_available("crypto", uid):
        kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال", callback_data="pm:crypto"))
    if is_gateway_available("tetrapay", uid):
        kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data="pm:tetrapay"))
    if is_gateway_available("swapwallet", uid):
        kb.add(types.InlineKeyboardButton("💎 پرداخت با سواپ ولت", callback_data="pm:swapwallet"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))

    user = get_user(uid)
    agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""

    send_or_edit(
        target,
        f"💳 <b>انتخاب روش پرداخت</b>\n\n"
        f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان{agent_note}\n\n"
        "روش پرداخت را انتخاب کنید:",
        kb
    )

def show_crypto_selection(target, amount=None):
    kb = types.InlineKeyboardMarkup()
    prices = fetch_crypto_prices() if amount else {}
    for coin_key, coin_label in CRYPTO_COINS:
        addr = setting_get(f"crypto_{coin_key}", "")
        if addr:
            symbol = CRYPTO_API_SYMBOLS.get(coin_key, "")
            price_note = ""
            if amount and symbol in prices and prices[symbol] > 0:
                coin_amount = amount / prices[symbol]
                price_note = f" ≈ {coin_amount:.4f} {symbol}"
            kb.add(types.InlineKeyboardButton(f"{coin_label}{price_note}", callback_data=f"pm:crypto:{coin_key}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="pm:back"))
    send_or_edit(target, "💎 <b>ارز دیجیتال</b>\n\nنوع ارز را انتخاب کنید:", kb)

def show_crypto_payment_info(target, uid, coin_key, amount):
    addr  = setting_get(f"crypto_{coin_key}", "")
    label = next((l for k, l in CRYPTO_COINS if k == coin_key), coin_key)
    symbol = CRYPTO_API_SYMBOLS.get(coin_key, "")
    if not addr:
        send_or_edit(target, "⚠️ آدرس این ارز هنوز توسط ادمین ثبت نشده است.", back_button("main"))
        return

    price_text = ""
    prices = fetch_crypto_prices()
    if symbol and symbol in prices and prices[symbol] > 0:
        coin_amount = amount / prices[symbol]
        price_text = (
            f"\n\n💱 <b>معادل ارزی:</b> <code>{coin_amount:.6f}</code> {symbol}\n"
            f"برای پرداخت با این ارز باید معادل <b>{coin_amount:.6f} {symbol}</b> واریز نمایید."
        )

    text = (
        f"💎 <b>پرداخت با {label}</b>\n\n"
        f"مبلغ: <b>{fmt_price(amount)}</b> تومان{price_text}\n\n"
        f"📋 آدرس ولت:\n<code>{esc(addr)}</code>\n\n"
        "پس از واریز، تصویر تراکنش یا هش آن را ارسال کنید."
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    send_or_edit(target, text, kb)

def send_payment_to_admins(payment_id):
    payment     = get_payment(payment_id)
    user        = get_user(payment["user_id"])
    package_row = get_package(payment["package_id"]) if payment["package_id"] else None
    kind_label  = "شارژ کیف پول" if payment["kind"] == "wallet_charge" else "خرید کانفیگ"
    method_label = payment["payment_method"]
    if payment["crypto_coin"]:
        method_label += f" ({payment['crypto_coin']})"
    package_text = ""
    if package_row:
        package_text = (
            f"\n🧩 نوع: {esc(package_row['type_name'])}"
            f"\n📦 پکیج: {esc(package_row['name'])}"
            f"\n🔋 حجم: {package_row['volume_gb']} گیگ"
            f"\n⏰ مدت: {package_row['duration_days']} روز"
        )
    text = (
        f"📥 <b>درخواست جدید برای بررسی</b>\n\n"
        f"🧾 نوع: {kind_label} | {method_label}\n"
        f"👤 کاربر: {esc(user['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
        f"🔢 آیدی: <code>{user['user_id']}</code>\n"
        f"💰 مبلغ: <b>{fmt_price(payment['amount'])}</b> تومان"
        f"{package_text}\n\n"
        f"📝 توضیح کاربر:\n{esc(payment['receipt_text'] or '-')}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ تأیید", callback_data=f"adm:pay:ap:{payment_id}"),
        types.InlineKeyboardButton("❌ رد",    callback_data=f"adm:pay:rj:{payment_id}"),
    )
    for admin_id in ADMIN_IDS:
        try:
            if payment["receipt_file_id"]:
                bot.send_photo(admin_id, payment["receipt_file_id"], caption=text, reply_markup=kb)
            else:
                bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            pass

def finish_card_payment_approval(payment_id, admin_note, approved):
    payment = get_payment(payment_id)
    if not payment or payment["status"] not in ("pending", "approved", "rejected"):
        return False
    user_id = payment["user_id"]
    if approved:
        approve_payment(payment_id, admin_note)
        if payment["kind"] == "wallet_charge":
            update_balance(user_id, payment["amount"])
            complete_payment(payment_id)
            bot.send_message(user_id, f"✅ واریزی شما تأیید شد.\n\n{esc(admin_note)}")
        elif payment["kind"] == "config_purchase":
            config_id   = payment["config_id"]
            package_id  = payment["package_id"]
            package_row = get_package(package_id)
            if not config_id:
                config_id = reserve_first_config(package_id, payment_id)
            if not config_id:
                bot.send_message(user_id, "❌ پرداخت تأیید شد اما موجودی کانفیگ تمام شده است. با پشتیبانی تماس بگیرید.")
                return False
            if payment["config_id"] != config_id:
                with get_conn() as conn:
                    conn.execute("UPDATE payments SET config_id=? WHERE id=?", (config_id, payment_id))
            purchase_id = assign_config_to_user(config_id, user_id, package_id, payment["amount"],
                                                payment["payment_method"], is_test=0)
            complete_payment(payment_id)
            bot.send_message(user_id, f"✅ واریزی شما تأیید شد.\n\n{esc(admin_note)}")
            deliver_purchase_message(user_id, purchase_id)
            admin_purchase_notify(payment["payment_method"], get_user(user_id), package_row)
        elif payment["kind"] == "renewal":
            package_id  = payment["package_id"]
            package_row = get_package(package_id)
            config_id   = payment["config_id"]
            complete_payment(payment_id)
            bot.send_message(user_id,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم.")
            # Find purchase for this config
            with get_conn() as conn:
                row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (config_id,)).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item = get_purchase(purchase_id) if purchase_id else None
            if item and package_row:
                admin_renewal_notify(user_id, item, package_row, payment["amount"], payment["payment_method"])
        return True
    else:
        reject_payment(payment_id, admin_note)
        if payment["config_id"]:
            release_reserved_config(payment["config_id"])
        bot.send_message(user_id, f"❌ رسید شما رد شد.\n\n{esc(admin_note)}")
        return True

# ── /start ─────────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start_handler(message):
    ensure_user(message.from_user)
    notify_first_start_if_needed(message.from_user)
    state_clear(message.from_user.id)
    if not check_channel_membership(message.from_user.id):
        channel_lock_message(message)
        return
    show_main_menu(message)

# ── Callback dispatcher ────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid  = call.from_user.id
    ensure_user(call.from_user)
    data = call.data or ""

    # Channel check button
    if data == "check_channel":
        if check_channel_membership(uid):
            bot.answer_callback_query(call.id, "✅ عضویت تأیید شد!")
            show_main_menu(call)
        else:
            bot.answer_callback_query(call.id, "❌ هنوز عضو کانال نشده‌اید.", show_alert=True)
        return

    if not check_channel_membership(uid):
        bot.answer_callback_query(call.id)
        channel_lock_message(call)
        return

    try:
        _dispatch_callback(call, uid, data)
    except Exception as e:
        print("CALLBACK_ERROR:", e)
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, "خطایی رخ داد.", show_alert=True)
        except Exception:
            pass

def _dispatch_callback(call, uid, data):
    # Navigation
    if data.startswith("nav:"):
        target = data[4:]
        state_clear(uid)
        bot.answer_callback_query(call.id)
        if target == "main":
            show_main_menu(call)
        else:
            _fake_call(call, target)
        return

    if data == "profile":
        bot.answer_callback_query(call.id)
        show_profile(call, uid)
        return

    if data == "support":
        bot.answer_callback_query(call.id)
        show_support(call)
        return

    # ── Agency request ────────────────────────────────────────────────────────
    if data == "agency:request":
        user = get_user(uid)
        if user and user["is_agent"]:
            bot.answer_callback_query(call.id, "شما در حال حاضر نماینده هستید.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📤 ارسال درخواست (بدون متن)", callback_data="agency:send_empty"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        state_set(uid, "agency_request_text")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🤝 <b>درخواست نمایندگی</b>\n\n"
            "لطفاً متن درخواست خود را ارسال کنید. موارد زیر را در متن ذکر کنید:\n\n"
            "📊 میزان فروش شما در روز یا هفته\n"
            "📢 کانال یا فروشگاهی که دارید (آدرس کانال تلگرام)\n"
            "🎧 آیدی پشتیبانی مجموعه شما\n"
            "📝 هر توضیح دیگری که لازم می‌دانید\n\n"
            "اگر نمی‌خواهید متنی بنویسید، دکمه زیر را بزنید:", kb)
        return

    if data == "agency:send_empty":
        state_clear(uid)
        user = get_user(uid)
        if user and user["is_agent"]:
            bot.answer_callback_query(call.id, "شما در حال حاضر نماینده هستید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        send_or_edit(call, "✅ درخواست نمایندگی شما ارسال شد.\n⏳ لطفاً منتظر بررسی ادمین باشید.", back_button("main"))
        # Notify admins
        text = (
            f"🤝 <b>درخواست نمایندگی جدید</b>\n\n"
            f"👤 نام: {esc(user['full_name'])}\n"
            f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
            f"🔢 آیدی: <code>{user['user_id']}</code>\n\n"
            f"📝 متن درخواست: <i>بدون متن</i>"
        )
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ تأیید", callback_data=f"agency:approve:{uid}"),
            types.InlineKeyboardButton("❌ رد", callback_data=f"agency:reject:{uid}"),
        )
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, text, reply_markup=kb)
            except Exception:
                pass
        return

    if data.startswith("agency:approve:"):
        if not is_admin(uid) or not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        state_set(uid, "agency_approve_note", target_user_id=target_uid)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏭ بدون پیام", callback_data=f"agency:approve_now:{target_uid}"))
        bot.send_message(call.message.chat.id,
            f"✅ در حال تأیید نمایندگی کاربر <code>{target_uid}</code>\n\n"
            "اگر می‌خواهید پیامی برای کاربر ارسال کنید، متن را بنویسید.\n"
            "در غیر این صورت دکمه زیر را بزنید:", reply_markup=kb)
        return

    if data.startswith("agency:approve_now:"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        state_clear(uid)
        with get_conn() as conn:
            conn.execute("UPDATE users SET is_agent=1 WHERE user_id=?", (target_uid,))
        bot.answer_callback_query(call.id, "✅ نمایندگی تأیید شد.")
        _show_admin_user_detail(call, target_uid)
        try:
            bot.send_message(target_uid, "🎉 <b>درخواست نمایندگی شما تأیید شد!</b>\n\nاکنون شما نماینده هستید.")
        except Exception:
            pass
        return

    if data.startswith("agency:reject:"):
        if not is_admin(uid) or not admin_has_perm(uid, "agency"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_uid = int(data.split(":")[2])
        state_set(uid, "agency_reject_reason", target_user_id=target_uid)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.message.chat.id,
            f"❌ در حال رد درخواست نمایندگی کاربر <code>{target_uid}</code>\n\n"
            "لطفاً دلیل رد را بنویسید:")
        return

    if data == "my_configs":
        bot.answer_callback_query(call.id)
        show_my_configs(call, uid)
        return

    if data.startswith("mycfg:"):
        purchase_id = int(data.split(":")[1])
        item = get_purchase(purchase_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        deliver_purchase_message(call.message.chat.id, purchase_id)
        return

    # ── Renewal flow ──────────────────────────────────────────────────────────
    if data.startswith("renew:") and not data.startswith("renew:p:") and not data.startswith("renew:confirm:"):
        purchase_id = int(data.split(":")[1])
        item = get_purchase(purchase_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        # Show packages of same type for renewal
        with get_conn() as conn:
            type_id = conn.execute("SELECT type_id FROM packages WHERE id=?", (item["package_id"],)).fetchone()["type_id"]
        packages = [p for p in get_packages(type_id=type_id) if p["price"] > 0]
        kb = types.InlineKeyboardMarkup()
        user = get_user(uid)
        for p in packages:
            price = get_effective_price(uid, p)
            title = f"{p['name']} | {p['volume_gb']}GB | {p['duration_days']} روز | {fmt_price(price)} ت"
            kb.add(types.InlineKeyboardButton(title, callback_data=f"renew:p:{purchase_id}:{p['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"mycfg:{purchase_id}"))
        bot.answer_callback_query(call.id)
        agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""
        if not packages:
            send_or_edit(call, "📭 در حال حاضر پکیجی برای تمدید موجود نیست.", kb)
        else:
            send_or_edit(call, f"♻️ <b>تمدید سرویس</b>\n\nپکیج مورد نظر برای تمدید را انتخاب کنید:{agent_note}", kb)
        return

    if data.startswith("renew:p:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        package_row = get_package(package_id)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        state_set(uid, "renew_select_method",
                  package_id=package_id, amount=price,
                  kind="renewal", purchase_id=purchase_id)
        text = (
            "♻️ <b>تمدید سرویس</b>\n\n"
            f"🔮 سرویس فعلی: {esc(item['service_name'])}\n"
            f"📦 پکیج تمدید: {esc(package_row['name'])}\n"
            f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
            f"⏰ مدت: {package_row['duration_days']} روز\n"
            f"💰 قیمت: {fmt_price(price)} تومان\n\n"
            "روش پرداخت را انتخاب کنید:"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💰 پرداخت از موجودی", callback_data=f"rpay:wallet:{purchase_id}:{package_id}"))
        if is_gateway_available("card", uid) and is_card_info_complete():
            kb.add(types.InlineKeyboardButton("💳 کارت به کارت", callback_data=f"rpay:card:{purchase_id}:{package_id}"))
        if is_gateway_available("crypto", uid):
            kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال", callback_data=f"rpay:crypto:{purchase_id}:{package_id}"))
        if is_gateway_available("tetrapay", uid):
            kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data=f"rpay:tetrapay:{purchase_id}:{package_id}"))
        if is_gateway_available("swapwallet", uid):
            kb.add(types.InlineKeyboardButton("💎 پرداخت با سواپ ولت", callback_data=f"rpay:swapwallet:{purchase_id}:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"renew:{purchase_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    # ── Renewal payment handlers ──────────────────────────────────────────────
    if data.startswith("rpay:wallet:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        user = get_user(uid)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if user["balance"] < price:
            bot.answer_callback_query(call.id, "موجودی کیف پول کافی نیست.", show_alert=True)
            return
        update_balance(uid, -price)
        payment_id = create_payment("renewal", uid, package_id, price, "wallet",
                                     status="completed", config_id=item["config_id"])
        complete_payment(payment_id)
        bot.answer_callback_query(call.id, "پرداخت موفق بود.")
        send_or_edit(call,
            "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
            "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
            "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
            "🙏 از صبر و شکیبایی شما متشکریم.",
            back_button("main"))
        admin_renewal_notify(uid, item, package_row, price, "کیف پول")
        state_clear(uid)
        return

    if data.startswith("rpay:card:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        card  = setting_get("payment_card", "")
        bank  = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        if not card:
            bot.answer_callback_query(call.id, "اطلاعات پرداخت هنوز ثبت نشده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        payment_id = create_payment("renewal", uid, package_id, price, "card", status="pending",
                                     config_id=item["config_id"])
        state_set(uid, "await_renewal_receipt", payment_id=payment_id, purchase_id=purchase_id)
        text = (
            "💳 <b>کارت به کارت (تمدید)</b>\n\n"
            f"لطفاً مبلغ <b>{fmt_price(price)}</b> تومان را به کارت زیر واریز کنید:\n\n"
            f"🏦 {esc(bank or 'ثبت نشده')}\n"
            f"👤 {esc(owner or 'ثبت نشده')}\n"
            f"💳 <code>{esc(card)}</code>\n\n"
            "📸 پس از واریز، تصویر رسید یا شماره پیگیری را ارسال کنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("rpay:crypto:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        state_set(uid, "renew_crypto_select_coin", package_id=package_id, amount=price,
                  purchase_id=purchase_id, config_id=item["config_id"])
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=price)
        return

    if data.startswith("rpay:swapwallet:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        success, inv = check_swapwallet_invoice(invoice_id)
        if not success:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        if inv.get("status") == "PAID":
            complete_payment(payment_id)
            package_row = get_package(payment["package_id"])
            config_id   = payment["config_id"]
            with get_conn() as conn:
                row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (config_id,)).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item = get_purchase(purchase_id) if purchase_id else None
            bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
            send_or_edit(call,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم.",
                back_button("main"))
            if item:
                admin_renewal_notify(uid, item, package_row, payment["amount"], "SwapWallet")
            state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا از سواپ ولت پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("rpay:swapwallet:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price    = get_effective_price(uid, package_row)
        order_id = f"rnw-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_swapwallet_invoice(price, order_id, f"تمدید {package_row['name']}")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد فاکتور سواپ ولت.", show_alert=True)
            return
        invoice_id     = result.get("id", "")
        wallet_address = result.get("walletAddress", "")
        links          = result.get("links", [])
        usd_val        = result.get("amount", {}).get("usdValue", {})
        usd_amount     = usd_val.get("number", "")
        usd_unit       = usd_val.get("unit", "USDT")
        payment_id = create_payment("renewal", uid, package_id, price, "swapwallet", status="pending",
                                     config_id=item["config_id"])
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_renewal_swapwallet_verify", payment_id=payment_id, invoice_id=invoice_id,
                  purchase_id=purchase_id)
        text = (
            "💎 <b>پرداخت با سواپ ولت (تمدید)</b>\n\n"
            "⚠️ <b>راهنما:</b>\n"
            "۱. ربات <a href='https://t.me/SwapWalletBot'>@SwapWalletBot</a> را استارت کنید\n"
            "۲. احراز هویت انجام دهید\n"
            "۳. کیف پول خود را به مقدار لازم شارژ کنید\n"
            "۴. روی دکمه «پرداخت» بزنید تا مبلغ از کیف پولتان کسر شود\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n"
            f"💵 معادل: <b>{esc(usd_amount)} {esc(usd_unit)}</b>\n\n"
            f"📬 آدرس کیف پول:\n<code>{esc(wallet_address)}</code>\n\n"
            "پس از پرداخت، دکمه «✅ بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        for link in links:
            link_name = link.get("name", "")
            link_url  = link.get("url", "")
            if link_url:
                btn_label = "💳 پرداخت از سواپ ولت" if link_name == "SWAP_WALLET" else f"🔗 {esc(link_name)}"
                kb.add(types.InlineKeyboardButton(btn_label, url=link_url))
        kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=f"rpay:swapwallet:verify:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("rpay:tetrapay:"):
        parts = data.split(":")
        purchase_id = int(parts[2])
        package_id  = int(parts[3])
        item = get_purchase(purchase_id)
        package_row = get_package(package_id)
        if not item or item["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        hash_id = f"rnw-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_tetrapay_order(price, hash_id, f"تمدید {package_row['name']}")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد درخواست پرداخت آنلاین.", show_alert=True)
            return
        authority = result.get("Authority", "")
        pay_url_bot = result.get("payment_url_bot", "")
        pay_url_web = result.get("payment_url_web", "")
        payment_id = create_payment("renewal", uid, package_id, price, "tetrapay", status="pending",
                                     config_id=item["config_id"])
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (authority, payment_id))
        state_set(uid, "await_renewal_tetrapay_verify", payment_id=payment_id, authority=authority,
                  purchase_id=purchase_id)
        text = (
            "🏦 <b>پرداخت آنلاین (تمدید)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n\n"
            "لطفاً از یکی از لینک‌های زیر پرداخت را انجام دهید.\n"
            "پس از پرداخت، دکمه «✅ بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        if pay_url_bot and setting_get("tetrapay_mode_bot", "1") == "1":
            kb.add(types.InlineKeyboardButton("💳 پرداخت در تلگرام", url=pay_url_bot))
        if pay_url_web and setting_get("tetrapay_mode_web", "1") == "1":
            kb.add(types.InlineKeyboardButton("🌐 پرداخت در مرورگر", url=pay_url_web))
        kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=f"rpay:tetrapay:verify:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("rpay:tetrapay:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        authority = payment["receipt_text"]
        success, result = verify_tetrapay_order(authority)
        if success:
            complete_payment(payment_id)
            package_row = get_package(payment["package_id"])
            config_id = payment["config_id"]
            with get_conn() as conn:
                row = conn.execute("SELECT purchase_id FROM configs WHERE id=?", (config_id,)).fetchone()
            purchase_id = row["purchase_id"] if row else 0
            item = get_purchase(purchase_id) if purchase_id else None
            bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
            send_or_edit(call,
                "✅ <b>درخواست تمدید ارسال شد</b>\n\n"
                "🔄 درخواست تمدید سرویس شما با موفقیت ثبت و برای پشتیبانی ارسال شد.\n"
                "⏳ لطفاً کمی صبر کنید، پس از انجام تمدید به شما اطلاع داده خواهد شد.\n\n"
                "🙏 از صبر و شکیبایی شما متشکریم.",
                back_button("main"))
            if item:
                admin_renewal_notify(uid, item, package_row, payment["amount"], "TetraPay")
            state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    # ── Admin: Confirm renewal ────────────────────────────────────────────────
    if data.startswith("renew:confirm:"):
        if not admin_has_perm(uid, "approve_renewal"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        parts = data.split(":")
        config_id  = int(parts[2])
        target_uid = int(parts[3])
        # Un-expire config if it was expired
        with get_conn() as conn:
            conn.execute("UPDATE configs SET is_expired=0 WHERE id=?", (config_id,))
        bot.answer_callback_query(call.id, "✅ تمدید تأیید شد.")
        # Update admin's message
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        try:
            bot.send_message(call.message.chat.id, "✅ تمدید تأیید و به کاربر اطلاع داده شد.")
        except Exception:
            pass
        # Notify user
        try:
            with get_conn() as conn:
                cfg_row = conn.execute("SELECT service_name FROM configs WHERE id=?", (config_id,)).fetchone()
            svc_name = cfg_row["service_name"] if cfg_row else ""
            bot.send_message(target_uid,
                f"🎉 <b>تمدید سرویس انجام شد!</b>\n\n"
                f"✅ سرویس <b>{esc(svc_name)}</b> شما با موفقیت تمدید شد.\n"
                "از اعتماد شما سپاسگزاریم. 🙏")
        except Exception:
            pass
        return

    # ── Buy flow ──────────────────────────────────────────────────────────────
    if data == "buy:start":
        # Check purchase rules
        if setting_get("purchase_rules_enabled", "0") == "1":
            accepted = setting_get(f"rules_accepted_{uid}", "0")
            if accepted != "1":
                rules_text = setting_get("purchase_rules_text", "")
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✅ من قوانین را خواندم و پذیرفتم", callback_data="buy:accept_rules"))
                kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
                bot.answer_callback_query(call.id)
                send_or_edit(call, f"📜 <b>قوانین خرید</b>\n\n{esc(rules_text)}", kb)
                return
        # Fall through to actual buy
        data = "buy:start_real"

    if data == "buy:start_real":
        items = get_all_types()
        kb = types.InlineKeyboardMarkup()
        has_any = False
        for item in items:
            # Only show types that have at least one package with stock
            packs = [p for p in get_packages(type_id=item['id']) if p['price'] > 0 and p['stock'] > 0]
            if packs:
                kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data=f"buy:t:{item['id']}"))
                has_any = True
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        if not has_any:
            send_or_edit(call, "📭 در حال حاضر بسته‌ای برای فروش موجود نیست.", kb)
        else:
            send_or_edit(call, "🛒 <b>خرید کانفیگ جدید</b>\n\nنوع مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("buy:t:"):
        type_id  = int(data.split(":")[2])
        packages = [p for p in get_packages(type_id=type_id) if p["price"] > 0 and p["stock"] > 0]
        kb       = types.InlineKeyboardMarkup()
        user     = get_user(uid)
        for p in packages:
            price = get_effective_price(uid, p)
            title = f"{p['name']} | {p['volume_gb']}GB | {p['duration_days']} روز | {fmt_price(price)} ت"
            kb.add(types.InlineKeyboardButton(title, callback_data=f"buy:p:{p['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="buy:start"))
        bot.answer_callback_query(call.id)
        agent_note = "\n\n🤝 <i>این قیمت‌ها مخصوص همکاری شماست</i>" if user and user["is_agent"] else ""
        if not packages:
            send_or_edit(call, "📭 در حال حاضر بسته‌ای برای فروش در این نوع موجود نیست.", kb)
        else:
            send_or_edit(call, f"📦 یکی از پکیج‌ها را انتخاب کنید:{agent_note}", kb)
        return

    if data.startswith("buy:p:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        state_set(uid, "buy_select_method",
                  package_id=package_id, amount=price,
                  kind="config_purchase")
        text = (
            "💳 <b>انتخاب روش پرداخت</b>\n\n"
            f"🧩 نوع: {esc(package_row['type_name'])}\n"
            f"📦 پکیج: {esc(package_row['name'])}\n"
            f"🔋 حجم: {package_row['volume_gb']} گیگ\n"
            f"⏰ مدت: {package_row['duration_days']} روز\n"
            f"💰 قیمت: {fmt_price(price)} تومان\n\n"
            "روش پرداخت را انتخاب کنید:"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💰 پرداخت از موجودی", callback_data=f"pay:wallet:{package_id}"))
        if is_gateway_available("card", uid) and is_card_info_complete():
            kb.add(types.InlineKeyboardButton("💳 کارت به کارت", callback_data=f"pay:card:{package_id}"))
        if is_gateway_available("crypto", uid):
            kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال", callback_data=f"pay:crypto:{package_id}"))
        if is_gateway_available("tetrapay", uid):
            kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data=f"pay:tetrapay:{package_id}"))
        if is_gateway_available("swapwallet", uid):
            kb.add(types.InlineKeyboardButton("💎 پرداخت با سواپ ولت", callback_data=f"pay:swapwallet:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"buy:t:{package_row['type_id']}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("pay:wallet:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        user        = get_user(uid)
        if not package_row or package_row["stock"] <= 0:
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        if user["balance"] < price:
            bot.answer_callback_query(call.id, "موجودی کیف پول کافی نیست.", show_alert=True)
            return
        config_id = reserve_first_config(package_id)
        if not config_id:
            bot.answer_callback_query(call.id, "فعلاً کانفیگی موجود نیست.", show_alert=True)
            return
        update_balance(uid, -price)
        purchase_id = assign_config_to_user(config_id, uid, package_id, price, "wallet", is_test=0)
        payment_id  = create_payment("config_purchase", uid, package_id, price, "wallet",
                                     status="completed", config_id=config_id)
        complete_payment(payment_id)
        bot.answer_callback_query(call.id, "خرید با موفقیت انجام شد.")
        send_or_edit(call, "✅ خرید شما انجام شد و سرویس در پیام بعدی ارسال می‌شود.", back_button("main"))
        deliver_purchase_message(call.message.chat.id, purchase_id)
        admin_purchase_notify("کیف پول", get_user(uid), package_row)
        state_clear(uid)
        return

    if data.startswith("pay:card:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or package_row["stock"] <= 0:
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        card  = setting_get("payment_card", "")
        bank  = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        if not card:
            bot.answer_callback_query(call.id, "اطلاعات پرداخت هنوز ثبت نشده است.", show_alert=True)
            return
        price      = get_effective_price(uid, package_row)
        payment_id = create_payment("config_purchase", uid, package_id, price, "card", status="pending")
        state_set(uid, "await_purchase_receipt", payment_id=payment_id)
        text = (
            "💳 <b>کارت به کارت</b>\n\n"
            f"لطفاً مبلغ <b>{fmt_price(price)}</b> تومان را به کارت زیر واریز کنید:\n\n"
            f"🏦 {esc(bank or 'ثبت نشده')}\n"
            f"👤 {esc(owner or 'ثبت نشده')}\n"
            f"💳 <code>{esc(card)}</code>\n\n"
            "📸 پس از واریز، تصویر رسید یا شماره پیگیری را ارسال کنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("pay:crypto:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or package_row["stock"] <= 0:
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        state_set(uid, "buy_crypto_select_coin", package_id=package_id, amount=price)
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=price)
        return

    # Crypto coin selection (after buy)
    if data.startswith("pm:crypto:"):
        coin_key = data.split(":")[2]
        sd       = state_data(uid)
        sn       = state_name(uid)
        if sn == "buy_crypto_select_coin":
            package_id  = sd.get("package_id")
            amount      = sd.get("amount")
            package_row = get_package(package_id)
            if not package_row or package_row["stock"] <= 0:
                bot.answer_callback_query(call.id, "موجودی تمام شده است.", show_alert=True)
                return
            payment_id = create_payment("config_purchase", uid, package_id, amount, "crypto",
                                        status="pending", crypto_coin=coin_key)
            state_set(uid, "await_purchase_receipt", payment_id=payment_id)
            bot.answer_callback_query(call.id)
            show_crypto_payment_info(call, uid, coin_key, amount)
        elif sn == "wallet_crypto_select_coin":
            amount     = sd.get("amount")
            payment_id = sd.get("payment_id") or create_payment("wallet_charge", uid, None, amount, "crypto",
                                                                  status="pending", crypto_coin=coin_key)
            state_set(uid, "await_wallet_receipt", payment_id=payment_id, amount=amount)
            bot.answer_callback_query(call.id)
            show_crypto_payment_info(call, uid, coin_key, amount)
        elif sn == "renew_crypto_select_coin":
            package_id  = sd.get("package_id")
            amount      = sd.get("amount")
            config_id_r = sd.get("config_id")
            purchase_id = sd.get("purchase_id")
            payment_id = create_payment("renewal", uid, package_id, amount, "crypto",
                                        status="pending", crypto_coin=coin_key, config_id=config_id_r)
            state_set(uid, "await_renewal_receipt", payment_id=payment_id, purchase_id=purchase_id)
            bot.answer_callback_query(call.id)
            show_crypto_payment_info(call, uid, coin_key, amount)
        else:
            bot.answer_callback_query(call.id)
        return

    if data == "pm:crypto":
        sd = state_data(uid)
        amount = sd.get("amount")
        if state_name(uid) == "wallet_charge_method":
            payment_id = create_payment("wallet_charge", uid, None, amount, "crypto", status="pending")
            state_set(uid, "wallet_crypto_select_coin", amount=amount, payment_id=payment_id)
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=amount)
        return

    if data == "pm:back":
        bot.answer_callback_query(call.id)
        show_main_menu(call)
        return

    # ── SwapWallet ────────────────────────────────────────────────────────────
    if data.startswith("pay:swapwallet:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        invoice_id = payment["receipt_text"]
        success, inv = check_swapwallet_invoice(invoice_id)
        if not success:
            bot.answer_callback_query(call.id, "خطا در بررسی وضعیت فاکتور.", show_alert=True)
            return
        if inv.get("status") == "PAID":
            if payment["kind"] == "wallet_charge":
                update_balance(uid, payment["amount"])
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, f"✅ پرداخت شما تأیید و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان", back_button("main"))
                state_clear(uid)
            else:
                config_id  = payment["config_id"]
                package_id = payment["package_id"]
                package_row = get_package(package_id)
                if not config_id:
                    config_id = reserve_first_config(package_id, payment_id)
                if not config_id:
                    bot.answer_callback_query(call.id, "پرداخت تأیید شد اما کانفیگ موجود نیست. با پشتیبانی تماس بگیرید.", show_alert=True)
                    return
                purchase_id = assign_config_to_user(config_id, uid, package_id, payment["amount"], "swapwallet", is_test=0)
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, "✅ پرداخت شما تأیید شد و سرویس آماده است.", back_button("main"))
                deliver_purchase_message(call.message.chat.id, purchase_id)
                admin_purchase_notify("SwapWallet", get_user(uid), package_row)
                state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا از سواپ ولت پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("pay:swapwallet:"):
        package_id  = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or package_row["stock"] <= 0:
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price    = get_effective_price(uid, package_row)
        order_id = f"cfg-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_swapwallet_invoice(price, order_id, f"خرید {package_row['name']}")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد فاکتور سواپ ولت.", show_alert=True)
            return
        invoice_id     = result.get("id", "")
        wallet_address = result.get("walletAddress", "")
        links          = result.get("links", [])
        usd_val        = result.get("amount", {}).get("usdValue", {})
        usd_amount     = usd_val.get("number", "")
        usd_unit       = usd_val.get("unit", "USDT")
        payment_id = create_payment("config_purchase", uid, package_id, price, "swapwallet", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_swapwallet_verify", payment_id=payment_id, invoice_id=invoice_id)
        text = (
            "💎 <b>پرداخت با سواپ ولت</b>\n\n"
            "⚠️ <b>راهنما:</b>\n"
            "۱. ربات <a href='https://t.me/SwapWalletBot'>@SwapWalletBot</a> را استارت کنید\n"
            "۲. احراز هویت انجام دهید\n"
            "۳. کیف پول خود را به مقدار لازم شارژ کنید\n"
            "۴. روی دکمه «پرداخت» بزنید تا مبلغ از کیف پولتان کسر شود\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n"
            f"💵 معادل: <b>{esc(usd_amount)} {esc(usd_unit)}</b>\n\n"
            f"📬 آدرس کیف پول:\n<code>{esc(wallet_address)}</code>\n\n"
            "پس از پرداخت، دکمه «✅ بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        for link in links:
            link_name = link.get("name", "")
            link_url  = link.get("url", "")
            if link_url:
                btn_label = "💳 پرداخت از سواپ ولت" if link_name == "SWAP_WALLET" else f"🔗 {esc(link_name)}"
                kb.add(types.InlineKeyboardButton(btn_label, url=link_url))
        kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=f"pay:swapwallet:verify:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    # ── TetraPay ──────────────────────────────────────────────────────────────
    if data.startswith("pay:tetrapay:verify:"):
        payment_id = int(data.split(":")[3])
        payment = get_payment(payment_id)
        if not payment or payment["user_id"] != uid:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        if payment["status"] != "pending":
            bot.answer_callback_query(call.id, "این پرداخت قبلاً پردازش شده.", show_alert=True)
            return
        authority = payment["receipt_text"]
        success, result = verify_tetrapay_order(authority)
        if success:
            if payment["kind"] == "wallet_charge":
                update_balance(uid, payment["amount"])
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, f"✅ پرداخت شما تأیید و کیف پول شارژ شد.\n\n💰 مبلغ: {fmt_price(payment['amount'])} تومان", back_button("main"))
                state_clear(uid)
            else:
                config_id = payment["config_id"]
                package_id = payment["package_id"]
                package_row = get_package(package_id)
                if not config_id:
                    config_id = reserve_first_config(package_id, payment_id)
                if not config_id:
                    bot.answer_callback_query(call.id, "پرداخت تأیید شد اما کانفیگ موجود نیست. با پشتیبانی تماس بگیرید.", show_alert=True)
                    return
                purchase_id = assign_config_to_user(config_id, uid, package_id, payment["amount"], "tetrapay", is_test=0)
                complete_payment(payment_id)
                bot.answer_callback_query(call.id, "✅ پرداخت تأیید شد!")
                send_or_edit(call, "✅ پرداخت شما تأیید شد و سرویس آماده است.", back_button("main"))
                deliver_purchase_message(call.message.chat.id, purchase_id)
                admin_purchase_notify("TetraPay", get_user(uid), package_row)
                state_clear(uid)
        else:
            bot.answer_callback_query(call.id, "❌ پرداخت هنوز تأیید نشده. لطفاً ابتدا پرداخت را انجام دهید.", show_alert=True)
        return

    if data.startswith("pay:tetrapay:"):
        package_id = int(data.split(":")[2])
        package_row = get_package(package_id)
        if not package_row or package_row["stock"] <= 0:
            bot.answer_callback_query(call.id, "موجودی این پکیج تمام شده است.", show_alert=True)
            return
        price = get_effective_price(uid, package_row)
        hash_id = f"cfg-{uid}-{package_id}-{int(datetime.now().timestamp())}"
        success, result = create_tetrapay_order(price, hash_id, f"خرید {package_row['name']}")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد درخواست پرداخت آنلاین.", show_alert=True)
            return
        authority = result.get("Authority", "")
        pay_url_bot = result.get("payment_url_bot", "")
        pay_url_web = result.get("payment_url_web", "")
        payment_id = create_payment("config_purchase", uid, package_id, price, "tetrapay", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (authority, payment_id))
        state_set(uid, "await_tetrapay_verify", payment_id=payment_id, authority=authority)
        text = (
            "🏦 <b>پرداخت آنلاین (TetraPay)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(price)}</b> تومان\n\n"
            "لطفاً از یکی از لینک‌های زیر پرداخت را انجام دهید.\n"
            "پس از پرداخت، دکمه «✅ بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        if pay_url_bot and setting_get("tetrapay_mode_bot", "1") == "1":
            kb.add(types.InlineKeyboardButton("💳 پرداخت در تلگرام", url=pay_url_bot))
        if pay_url_web and setting_get("tetrapay_mode_web", "1") == "1":
            kb.add(types.InlineKeyboardButton("🌐 پرداخت در مرورگر", url=pay_url_web))
        kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=f"pay:tetrapay:verify:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    # ── Free test ─────────────────────────────────────────────────────────────
    if data == "test:start":
        if setting_get("free_test_enabled", "1") != "1":
            bot.answer_callback_query(call.id, "تست رایگان غیرفعال است.", show_alert=True)
            return
        user = get_user(uid)
        is_agent_user = user and user["is_agent"]
        if is_agent_user:
            agent_limit = int(setting_get("agent_test_limit", "0") or "0")
            agent_period = setting_get("agent_test_period", "day")
            if agent_limit > 0:
                used = agent_test_count_in_period(uid, agent_period)
                if used >= agent_limit:
                    period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
                    bot.answer_callback_query(call.id,
                        f"شما سقف تست رایگان ({agent_limit} عدد در {period_labels.get(agent_period, agent_period)}) را استفاده کرده‌اید.",
                        show_alert=True)
                    return
        else:
            if user_has_any_test(uid):
                bot.answer_callback_query(call.id, "شما قبلاً تست رایگان خود را دریافت کرده‌اید.", show_alert=True)
                return
        items = get_all_types()
        kb    = types.InlineKeyboardMarkup()
        has_any = False
        for item in items:
            packs = [p for p in get_packages(type_id=item['id'], price_only=0) if p['stock'] > 0]
            if packs:
                kb.add(types.InlineKeyboardButton(f"🎁 {item['name']}", callback_data=f"test:t:{item['id']}"))
                has_any = True
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        if not has_any:
            send_or_edit(call, "📭 در حال حاضر تست رایگانی موجود نیست.", kb)
        else:
            send_or_edit(call, "🎁 <b>تست رایگان</b>\n\nنوع مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("test:t:"):
        if setting_get("free_test_enabled", "1") != "1":
            bot.answer_callback_query(call.id, "تست رایگان غیرفعال است.", show_alert=True)
            return
        user = get_user(uid)
        is_agent_user = user and user["is_agent"]
        if is_agent_user:
            agent_limit = int(setting_get("agent_test_limit", "0") or "0")
            agent_period = setting_get("agent_test_period", "day")
            if agent_limit > 0:
                used = agent_test_count_in_period(uid, agent_period)
                if used >= agent_limit:
                    period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
                    bot.answer_callback_query(call.id,
                        f"شما سقف تست رایگان ({agent_limit} عدد در {period_labels.get(agent_period, agent_period)}) را استفاده کرده‌اید.",
                        show_alert=True)
                    return
        else:
            if user_has_any_test(uid):
                bot.answer_callback_query(call.id, "شما قبلاً تست رایگان خود را دریافت کرده‌اید.", show_alert=True)
                return
        type_id     = int(data.split(":")[2])
        type_row    = get_type(type_id)
        package_row = None
        for item in get_packages(type_id=type_id, price_only=0):
            if item["stock"] > 0:
                package_row = item
                break
        if not package_row:
            bot.answer_callback_query(call.id, "برای این نوع تست رایگان موجود نیست.", show_alert=True)
            return
        config_id = reserve_first_config(package_row["id"])
        if not config_id:
            bot.answer_callback_query(call.id, "تست رایگان این نوع تمام شده است.", show_alert=True)
            return
        purchase_id = assign_config_to_user(config_id, uid, package_row["id"], 0, "free_test", is_test=1)
        bot.answer_callback_query(call.id, "تست رایگان ارسال شد.")
        send_or_edit(call, f"✅ تست رایگان نوع <b>{esc(type_row['name'])}</b> آماده شد.", back_button("main"))
        deliver_purchase_message(call.message.chat.id, purchase_id)
        return

    # ── Wallet charge ─────────────────────────────────────────────────────────
    if data == "wallet:charge":
        state_set(uid, "await_wallet_amount")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💳 <b>شارژ کیف پول</b>\n\nمبلغ مورد نظر را به تومان وارد کنید:", kb)
        return

    if data == "wallet:charge:card":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        card  = setting_get("payment_card", "")
        bank  = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        if not card:
            bot.answer_callback_query(call.id, "اطلاعات پرداخت هنوز ثبت نشده است.", show_alert=True)
            return
        payment_id = create_payment("wallet_charge", uid, None, amount, "card", status="pending")
        state_set(uid, "await_wallet_receipt", payment_id=payment_id, amount=amount)
        text = (
            "💳 <b>کارت به کارت</b>\n\n"
            f"لطفاً مبلغ <b>{fmt_price(amount)}</b> تومان را به کارت زیر واریز کنید:\n\n"
            f"🏦 {esc(bank or 'ثبت نشده')}\n"
            f"👤 {esc(owner or 'ثبت نشده')}\n"
            f"💳 <code>{esc(card)}</code>\n\n"
            "📸 پس از واریز، تصویر رسید یا شماره پیگیری را ارسال کنید."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "wallet:charge:crypto":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        state_set(uid, "wallet_crypto_select_coin", amount=amount)
        bot.answer_callback_query(call.id)
        show_crypto_selection(call, amount=amount)
        return

    if data == "wallet:charge:tetrapay":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        hash_id = f"wallet-{uid}-{int(datetime.now().timestamp())}"
        success, result = create_tetrapay_order(amount, hash_id, "شارژ کیف پول")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد درخواست پرداخت آنلاین.", show_alert=True)
            return
        authority = result.get("Authority", "")
        pay_url_bot = result.get("payment_url_bot", "")
        pay_url_web = result.get("payment_url_web", "")
        payment_id = create_payment("wallet_charge", uid, None, amount, "tetrapay", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (authority, payment_id))
        state_set(uid, "await_tetrapay_verify", payment_id=payment_id, authority=authority)
        text = (
            "🏦 <b>شارژ کیف پول - پرداخت آنلاین (TetraPay)</b>\n\n"
            f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان\n\n"
            "لطفاً از یکی از لینک‌های زیر پرداخت را انجام دهید.\n"
            "پس از پرداخت، دکمه «✅ بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        if pay_url_bot and setting_get("tetrapay_mode_bot", "1") == "1":
            kb.add(types.InlineKeyboardButton("💳 پرداخت در تلگرام", url=pay_url_bot))
        if pay_url_web and setting_get("tetrapay_mode_web", "1") == "1":
            kb.add(types.InlineKeyboardButton("🌐 پرداخت در مرورگر", url=pay_url_web))
        kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=f"pay:tetrapay:verify:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "wallet:charge:swapwallet":
        sd     = state_data(uid)
        amount = sd.get("amount")
        if not amount:
            bot.answer_callback_query(call.id, "ابتدا مبلغ را وارد کنید.", show_alert=True)
            return
        order_id = f"wallet-{uid}-{int(datetime.now().timestamp())}"
        success, result = create_swapwallet_invoice(amount, order_id, "شارژ کیف پول")
        if not success:
            bot.answer_callback_query(call.id, "خطا در ایجاد فاکتور سواپ ولت.", show_alert=True)
            return
        invoice_id     = result.get("id", "")
        wallet_address = result.get("walletAddress", "")
        links          = result.get("links", [])
        usd_val        = result.get("amount", {}).get("usdValue", {})
        usd_amount     = usd_val.get("number", "")
        usd_unit       = usd_val.get("unit", "USDT")
        payment_id = create_payment("wallet_charge", uid, None, amount, "swapwallet", status="pending")
        with get_conn() as conn:
            conn.execute("UPDATE payments SET receipt_text=? WHERE id=?", (invoice_id, payment_id))
        state_set(uid, "await_swapwallet_verify", payment_id=payment_id, invoice_id=invoice_id)
        text = (
            "💎 <b>شارژ کیف پول - پرداخت با سواپ ولت</b>\n\n"
            "⚠️ <b>راهنما:</b>\n"
            "۱. ربات <a href='https://t.me/SwapWalletBot'>@SwapWalletBot</a> را استارت کنید\n"
            "۲. احراز هویت انجام دهید\n"
            "۳. کیف پول خود را شارژ کنید\n"
            "۴. روی دکمه «پرداخت» بزنید تا مبلغ از کیف پولتان کسر شود\n\n"
            f"💰 مبلغ: <b>{fmt_price(amount)}</b> تومان\n"
            f"💵 معادل: <b>{esc(usd_amount)} {esc(usd_unit)}</b>\n\n"
            f"📬 آدرس کیف پول:\n<code>{esc(wallet_address)}</code>\n\n"
            "پس از پرداخت، دکمه «✅ بررسی پرداخت» را بزنید."
        )
        kb = types.InlineKeyboardMarkup()
        for link in links:
            link_name = link.get("name", "")
            link_url  = link.get("url", "")
            if link_url:
                btn_label = "💳 پرداخت از سواپ ولت" if link_name == "SWAP_WALLET" else f"🔗 {esc(link_name)}"
                kb.add(types.InlineKeyboardButton(btn_label, url=link_url))
        kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=f"pay:swapwallet:verify:{payment_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    # ── Admin panel ────────────────────────────────────────────────────────────
    if not is_admin(uid):
        # Non-admin shouldn't reach admin callbacks, just ignore
        if data.startswith("admin:") or data.startswith("adm:"):
            bot.answer_callback_query(call.id, "اجازه دسترسی ندارید.", show_alert=True)
            return

    if data == "admin:panel":
        bot.answer_callback_query(call.id)
        text = (
            "⚙️ <b>پنل مدیریت</b>\n\n"
            "بخش مورد نظر را انتخاب کنید:\n\n"
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            "💡 <b>ConfigFlow</b> \n"
            "👨‍💻 Developer: @Emad_Habibnia\n"
            "🌐 <a href='https://github.com/Emadhabibnia1385/ConfigFlow'>GitHub ConfigFlow</a>\n"
            "❤️ <a href='https://t.me/EmadHabibnia/4'>donate</a>"
        )
        send_or_edit(call, text, kb_admin_panel(uid))
        return

    # ── Admin: Types ──────────────────────────────────────────────────────────
    if data == "admin:types":
        if not admin_has_perm(uid, "types_packages"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        _show_admin_types(call)
        bot.answer_callback_query(call.id)
        return

    if data == "admin:type:add":
        state_set(uid, "admin_add_type")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🧩 نام نوع جدید را ارسال کنید:", back_button("admin:types"))
        return

    if data.startswith("admin:type:edit:"):
        type_id = int(data.split(":")[3])
        row     = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"admin:type:editname:{type_id}"))
        kb.add(types.InlineKeyboardButton("📝 ویرایش توضیحات", callback_data=f"admin:type:editdesc:{type_id}"))
        if row.get("description"):
            kb.add(types.InlineKeyboardButton("🗑 حذف توضیحات", callback_data=f"admin:type:deldesc:{type_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:types"))
        desc_preview = f"\n📝 توضیحات: {esc(row['description'][:80])}..." if row.get("description") and len(row["description"]) > 80 else (f"\n📝 توضیحات: {esc(row['description'])}" if row.get("description") else "\n📝 توضیحات: ندارد")
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ <b>ویرایش نوع:</b> {esc(row['name'])}{desc_preview}", kb)
        return

    if data.startswith("admin:type:editname:"):
        type_id = int(data.split(":")[3])
        row     = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        state_set(uid, "admin_edit_type", type_id=type_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ نام جدید برای نوع <b>{esc(row['name'])}</b> را ارسال کنید:",
                     back_button("admin:types"))
        return

    if data.startswith("admin:type:editdesc:"):
        type_id = int(data.split(":")[3])
        row     = get_type(type_id)
        if not row:
            bot.answer_callback_query(call.id, "نوع یافت نشد.", show_alert=True)
            return
        state_set(uid, "admin_edit_type_desc", type_id=type_id)
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏭ توضیحاتی نمی‌خواهم وارد کنم", callback_data=f"admin:type:deldesc:{type_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin:type:edit:{type_id}"))
        send_or_edit(call,
            f"📝 توضیحات جدید برای نوع <b>{esc(row['name'])}</b> را ارسال کنید:\n\n"
            "این توضیحات پس از ارسال کانفیگ به کاربر نمایش داده می‌شود.", kb)
        return

    if data == "admin:type:skipdesc":
        sn = state_name(uid)
        sd_val = state_data(uid)
        if sn == "admin_add_type_desc":
            name = sd_val.get("type_name", "")
            try:
                add_type(name, "")
                state_clear(uid)
                bot.answer_callback_query(call.id, "✅ نوع ثبت شد.")
                bot.send_message(call.message.chat.id, "✅ نوع جدید ثبت شد.", reply_markup=kb_admin_panel())
            except sqlite3.IntegrityError:
                state_clear(uid)
                bot.answer_callback_query(call.id, "⚠️ این نوع قبلاً ثبت شده.", show_alert=True)
        else:
            bot.answer_callback_query(call.id)
        return

    if data.startswith("admin:type:deldesc:"):
        type_id = int(data.split(":")[3])
        update_type_description(type_id, "")
        state_clear(uid)
        bot.answer_callback_query(call.id, "✅ توضیحات حذف شد.")
        _show_admin_types(call)
        return

    if data.startswith("admin:type:del:"):
        type_id = int(data.split(":")[3])
        delete_type(type_id)
        bot.answer_callback_query(call.id, "نوع حذف شد.")
        _show_admin_types(call)
        return

    if data.startswith("admin:pkg:add:t:"):
        type_id  = int(data.split(":")[4])
        type_row = get_type(type_id)
        state_set(uid, "admin_add_package_name", type_id=type_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ نام پکیج برای نوع <b>{esc(type_row['name'])}</b> را وارد کنید:",
                     back_button("admin:types"))
        return

    if data.startswith("admin:pkg:edit:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        if not package_row:
            bot.answer_callback_query(call.id, "پکیج یافت نشد.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✏️ ویرایش نام",   callback_data=f"admin:pkg:ef:name:{package_id}"))
        kb.add(types.InlineKeyboardButton("💰 ویرایش قیمت",  callback_data=f"admin:pkg:ef:price:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔋 ویرایش حجم",   callback_data=f"admin:pkg:ef:volume:{package_id}"))
        kb.add(types.InlineKeyboardButton("⏰ ویرایش مدت",   callback_data=f"admin:pkg:ef:dur:{package_id}"))
        kb.add(types.InlineKeyboardButton("� جایگاه نمایش",  callback_data=f"admin:pkg:ef:position:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",       callback_data="admin:types"))
        bot.answer_callback_query(call.id)
        cur_pos = package_row['position'] if 'position' in package_row.keys() else 0
        text = (
            f"📦 <b>ویرایش پکیج</b>\n\n"
            f"نام: {esc(package_row['name'])}\n"
            f"قیمت: {fmt_price(package_row['price'])} تومان\n"
            f"حجم: {package_row['volume_gb']} GB\n"
            f"مدت: {package_row['duration_days']} روز\n"
            f"جایگاه: {cur_pos}"
        )
        send_or_edit(call, text, kb)
        return

    if data.startswith("admin:pkg:ef:"):
        parts      = data.split(":")
        field_key  = parts[3]
        package_id = int(parts[4])
        state_set(uid, "admin_edit_pkg_field", field_key=field_key, package_id=package_id)
        labels     = {"name": "نام", "price": "قیمت (تومان)", "volume": "حجم (GB)", "dur": "مدت (روز)", "position": "جایگاه نمایش"}
        bot.answer_callback_query(call.id)
        send_or_edit(call, f"✏️ مقدار جدید برای <b>{labels.get(field_key, field_key)}</b> را وارد کنید:",
                     back_button("admin:types"))
        return

    if data.startswith("admin:pkg:del:"):
        package_id = int(data.split(":")[3])
        with get_conn() as conn:
            sold_count = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL",
                (package_id,)
            ).fetchone()["n"]
            if sold_count > 0:
                bot.answer_callback_query(call.id, f"❌ این پکیج {sold_count} کانفیگ فروخته‌شده دارد و قابل حذف نیست.", show_alert=True)
                return
            active_configs = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NULL AND is_expired=0 AND reserved_payment_id IS NULL",
                (package_id,)
            ).fetchone()["n"]
            if active_configs > 0:
                bot.answer_callback_query(call.id, f"❌ این پکیج {active_configs} کانفیگ فعال دارد و قابل حذف نیست.", show_alert=True)
                return
        delete_package(package_id)
        bot.answer_callback_query(call.id, "پکیج حذف شد.")
        _show_admin_types(call)
        return

    # ── Admin: Add Config ─────────────────────────────────────────────────────
    if data == "admin:add_config":
        types_list = get_all_types()
        kb = types.InlineKeyboardMarkup()
        for item in types_list:
            kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data=f"adm:cfg:t:{item['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📝 <b>ثبت کانفیگ</b>\n\nنوع کانفیگ را انتخاب کنید:", kb)
        return

    if data.startswith("adm:cfg:t:"):
        type_id = int(data.split(":")[3])
        packs   = get_packages(type_id=type_id)
        kb      = types.InlineKeyboardMarkup()
        for p in packs:
            kb.add(types.InlineKeyboardButton(
                f"{p['name']} | {p['volume_gb']}GB | {p['duration_days']}روز",
                callback_data=f"adm:cfg:p:{p['id']}"
            ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 پکیج مربوطه را انتخاب کنید:", kb)
        return

    if data.startswith("adm:cfg:p:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📝 ثبت تکی", callback_data=f"adm:cfg:single:{package_id}"))
        kb.add(types.InlineKeyboardButton("📋 ثبت دسته‌ای", callback_data=f"adm:cfg:bulk:{package_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:cfg:t:{package_row['type_id']}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📝 روش ثبت کانفیگ را انتخاب کنید:", kb)
        return

    if data.startswith("adm:cfg:single:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        state_set(uid, "admin_add_config_service", package_id=package_id, type_id=package_row["type_id"])
        bot.answer_callback_query(call.id)
        send_or_edit(call, "✏️ نام سرویس را وارد کنید:", back_button("admin:add_config"))
        return

    if data.startswith("adm:cfg:bulk:"):
        # Could be adm:cfg:bulk:{pkg_id} or adm:cfg:bulk:inq:y/n:{pkg_id} or adm:cfg:bulk:skip:...
        rest = data[len("adm:cfg:bulk:"):]

        # Skip prefix
        if rest.startswith("skippre:"):
            pkg_id = int(rest.split(":")[1])
            s = state_data(uid)
            state_set(uid, "admin_bulk_suffix",
                      package_id=s["package_id"], type_id=s["type_id"],
                      has_inquiry=s["has_inquiry"], count=s["count"], prefix="")
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                "✂️ <b>پسوند حذفی از نام کانفیگ</b>\n\n"
                "وقتی چندتا اکسترنال پروکسی ست می‌کنید، انتهای نام کانفیگ متن‌های اضافه اکسترنال‌ها اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن‌ها در نام کانفیگ بیاید، پسوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>-main</code>",
                back_button("admin:add_config"))
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ بعدی (بدون پسوند)", callback_data=f"adm:cfg:bulk:skipsuf:{pkg_id}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
            send_or_edit(call,
                "✂️ <b>پسوند حذفی از نام کانفیگ</b>\n\n"
                "وقتی چندتا اکسترنال پروکسی ست می‌کنید، انتهای نام کانفیگ متن‌های اضافه اکسترنال‌ها اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن‌ها در نام کانفیگ بیاید، پسوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>-main</code>", kb)
            return

        # Skip suffix
        if rest.startswith("skipsuf:"):
            pkg_id = int(rest.split(":")[1])
            s = state_data(uid)
            has_inq = s.get("has_inquiry", False)
            count = s.get("count", 0)
            prefix = s.get("prefix", "")
            state_set(uid, "admin_bulk_data",
                      package_id=s["package_id"], type_id=s["type_id"],
                      has_inquiry=has_inq, count=count, prefix=prefix, suffix="")
            bot.answer_callback_query(call.id)
            if has_inq:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    f"تعداد: <b>{count}</b> کانفیگ\n\n"
                    "هر کانفیگ <b>دو خط</b> دارد:\n"
                    "خط اول: لینک کانفیگ\n"
                    "خط دوم: لینک استعلام (شروع با http)\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "http://panel.com/sub/1\n"
                    "vless://def...#name2\n"
                    "http://panel.com/sub/2</code>"
                )
            else:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    f"تعداد: <b>{count}</b> کانفیگ\n\n"
                    "هر خط یک لینک کانفیگ:\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "vless://def...#name2</code>"
                )
            send_or_edit(call, fmt_text, back_button("admin:add_config"))
            return

        # Inquiry yes/no
        if rest.startswith("inq:"):
            sub_parts = rest.split(":")
            yn = sub_parts[1]
            pkg_id = int(sub_parts[2])
            has_inq = (yn == "y")
            state_set(uid, "admin_bulk_count",
                      package_id=pkg_id, type_id=state_data(uid).get("type_id", 0),
                      has_inquiry=has_inq)
            bot.answer_callback_query(call.id)
            send_or_edit(call, "🔢 تعداد کانفیگ‌ها را وارد کنید:", back_button("admin:add_config"))
            return

        # Initial: ask about inquiry links
        package_id  = int(rest)
        package_row = get_package(package_id)
        state_set(uid, "admin_bulk_init", package_id=package_id, type_id=package_row["type_id"])
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ بله", callback_data=f"adm:cfg:bulk:inq:y:{package_id}"),
            types.InlineKeyboardButton("❌ خیر", callback_data=f"adm:cfg:bulk:inq:n:{package_id}"),
        )
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:cfg:p:{package_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔗 آیا کانفیگ‌ها <b>لینک استعلام</b> هم دارند؟", kb)
        return

    # ── Admin: Stock / Config management ─────────────────────────────────────
    if data == "admin:stock":
        if not (admin_has_perm(uid, "view_configs") or admin_has_perm(uid, "register_config") or admin_has_perm(uid, "manage_configs")):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        _show_admin_stock(call)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("adm:stk:all:"):
        parts     = data.split(":")
        kind_str  = parts[3]
        page      = int(parts[4])
        # Query all configs across packages
        offset = page * CONFIGS_PER_PAGE
        with get_conn() as conn:
            if kind_str == "sl":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) AS n FROM configs WHERE sold_to IS NOT NULL").fetchone()["n"]
            elif kind_str == "ex":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) AS n FROM configs WHERE is_expired=1").fetchone()["n"]
            else:
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id ASC LIMIT ? OFFSET ?",
                    (CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) AS n FROM configs WHERE sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0").fetchone()["n"]
        total_pages = max(1, (total + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE)
        kb         = types.InlineKeyboardMarkup()
        for c in cfgs:
            expired_mark = " ❌" if c["is_expired"] else ""
            label = f"{c['service_name']}{expired_mark}"
            kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:stk:cfg:{c['id']}"))
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton("⬅️ قبلی", callback_data=f"adm:stk:all:{kind_str}:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton("بعدی ➡️", callback_data=f"adm:stk:all:{kind_str}:{page+1}"))
        if nav_row:
            kb.row(*nav_row)
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        bot.answer_callback_query(call.id)
        if kind_str == "sl":
            label_kind = "🔴 کل فروخته شده"
        elif kind_str == "ex":
            label_kind = "❌ کل منقضی شده"
        else:
            label_kind = "🟢 کل موجود"
        send_or_edit(call, f"📋 {label_kind} | صفحه {page+1}/{total_pages} | تعداد کل: {total}", kb)
        return

    if data.startswith("adm:stk:pk:"):
        package_id  = int(data.split(":")[3])
        package_row = get_package(package_id)
        avail = count_configs(package_id, sold=False)
        sold  = count_configs(package_id, sold=True)
        with get_conn() as conn:
            expired = conn.execute(
                "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND is_expired=1",
                (package_id,)
            ).fetchone()["n"]
        kb    = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"🟢 مانده ({avail})",       callback_data=f"adm:stk:av:{package_id}:0"),
            types.InlineKeyboardButton(f"🔴 فروخته ({sold})",       callback_data=f"adm:stk:sl:{package_id}:0"),
        )
        kb.add(types.InlineKeyboardButton(f"❌ منقضی ({expired})",  callback_data=f"adm:stk:ex:{package_id}:0"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        bot.answer_callback_query(call.id)
        text = (
            f"📦 <b>{esc(package_row['name'])}</b>\n\n"
            f"🟢 موجود: {avail}\n"
            f"🔴 فروخته شده: {sold}\n"
            f"❌ منقضی شده: {expired}"
        )
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:stk:av:") or data.startswith("adm:stk:sl:") or data.startswith("adm:stk:ex:"):
        parts      = data.split(":")
        kind_str   = parts[2]
        package_id = int(parts[3])
        page       = int(parts[4])
        offset     = page * CONFIGS_PER_PAGE
        with get_conn() as conn:
            if kind_str == "sl":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE package_id=? AND sold_to IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
                    (package_id, CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NOT NULL",
                    (package_id,)
                ).fetchone()["n"]
            elif kind_str == "ex":
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE package_id=? AND is_expired=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (package_id, CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND is_expired=1",
                    (package_id,)
                ).fetchone()["n"]
            else:
                cfgs = conn.execute(
                    "SELECT * FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0 ORDER BY id ASC LIMIT ? OFFSET ?",
                    (package_id, CONFIGS_PER_PAGE, offset)
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM configs WHERE package_id=? AND sold_to IS NULL AND reserved_payment_id IS NULL AND is_expired=0",
                    (package_id,)
                ).fetchone()["n"]
        total_pages = max(1, (total + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE)
        kb         = types.InlineKeyboardMarkup()
        for c in cfgs:
            expired_mark = " ❌" if c["is_expired"] else ""
            label = f"{c['service_name']}{expired_mark}"
            kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:stk:cfg:{c['id']}"))
        # Pagination
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton("⬅️ قبل", callback_data=f"adm:stk:{kind_str}:{package_id}:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton("بعد ➡️", callback_data=f"adm:stk:{kind_str}:{package_id}:{page+1}"))
        if nav_row:
            kb.row(*nav_row)
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:stk:pk:{package_id}"))
        bot.answer_callback_query(call.id)
        if kind_str == "sl":
            label_kind = "🔴 فروخته شده"
        elif kind_str == "ex":
            label_kind = "❌ منقضی شده"
        else:
            label_kind = "🟢 موجود"
        send_or_edit(call, f"📋 {label_kind} | صفحه {page+1}/{total_pages} | تعداد کل: {total}", kb)
        return

    if data.startswith("adm:stk:cfg:"):
        config_id = int(data.split(":")[3])
        with get_conn() as conn:
            row = conn.execute(
                """SELECT c.*, p.name AS pkg_name, p.volume_gb, p.duration_days, t.name AS type_name
                   FROM configs c
                   JOIN packages p ON p.id=c.package_id
                   JOIN config_types t ON t.id=c.type_id
                   WHERE c.id=?""",
                (config_id,)
            ).fetchone()
        if not row:
            bot.answer_callback_query(call.id, "یافت نشد.", show_alert=True)
            return
        text = (
            f"🔮 نام سرویس: <b>{esc(row['service_name'])}</b>\n"
            f"🧩 نوع سرویس: {esc(row['type_name'])}\n"
            f"🔋 حجم: {row['volume_gb']} گیگ\n"
            f"⏰ مدت: {row['duration_days']} روز\n\n"
            f"💝 Config:\n<code>{esc(row['config_text'])}</code>\n\n"
            f"🔋 Volume web: {esc(row['inquiry_link'] or '-')}\n"
            f"🗓 ثبت: {esc(row['created_at'])}"
        )
        kb = types.InlineKeyboardMarkup()
        if row["sold_to"]:
            buyer = get_user_detail(row["sold_to"])
            if buyer:
                text += (
                    f"\n\n🛒 <b>خریدار:</b>\n"
                    f"نام: {esc(buyer['full_name'])}\n"
                    f"نام کاربری: {esc(display_username(buyer['username']))}\n"
                    f"آیدی: <code>{buyer['user_id']}</code>\n"
                    f"زمان خرید: {esc(row['sold_at'] or '-')}"
                )
        if not row["is_expired"]:
            kb.add(types.InlineKeyboardButton("❌ منقضی کردن", callback_data=f"adm:stk:exp:{config_id}"))
        else:
            text += "\n\n⚠️ این سرویس منقضی شده است."
        kb.add(types.InlineKeyboardButton("🗑 حذف کانفیگ", callback_data=f"adm:stk:del:{config_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        bot.answer_callback_query(call.id)
        # Send with QR code
        try:
            qr_img = qrcode.make(row['config_text'])
            bio = io.BytesIO()
            qr_img.save(bio, format="PNG")
            bio.seek(0)
            bio.name = "qrcode.png"
            chat_id = call.message.chat.id
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            bot.send_photo(chat_id, bio, caption=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            send_or_edit(call, text, kb)
        return

    if data.startswith("adm:stk:exp:"):
        config_id = int(data.split(":")[3])
        expire_config(config_id)
        # Notify buyer if any
        with get_conn() as conn:
            row = conn.execute("SELECT sold_to FROM configs WHERE id=?", (config_id,)).fetchone()
        if row and row["sold_to"]:
            try:
                bot.send_message(
                    row["sold_to"],
                    "⚠️ یکی از سرویس‌های شما توسط ادمین منقضی اعلام شده است.\nبرای تمدید با پشتیبانی تماس بگیرید."
                )
            except Exception:
                pass
        bot.answer_callback_query(call.id, "سرویس منقضی شد.")
        send_or_edit(call, "✅ سرویس منقضی اعلام شد.", back_button("admin:stock"))
        return

    if data.startswith("adm:stk:del:"):
        config_id = int(data.split(":")[3])
        with get_conn() as conn:
            conn.execute("DELETE FROM configs WHERE id=?", (config_id,))
        bot.answer_callback_query(call.id, "کانفیگ حذف شد.")
        send_or_edit(call, "✅ کانفیگ با موفقیت حذف شد.", back_button("admin:stock"))
        return

    # ── Admin: Stock Search ───────────────────────────────────────────────────
    if data == "adm:stk:search":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔗 لینک استعلام", callback_data="adm:stk:srch:link"))
        kb.add(types.InlineKeyboardButton("💝 متن کانفیگ", callback_data="adm:stk:srch:cfg"))
        kb.add(types.InlineKeyboardButton("🔮 نام سرویس", callback_data="adm:stk:srch:name"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:stock"))
        send_or_edit(call, "🔍 جستجو بر اساس:", kb)
        bot.answer_callback_query(call.id)
        return

    if data == "adm:stk:srch:link":
        state_set(call.from_user.id, "admin_search_by_link")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔗 لینک استعلام (یا بخشی از آن) را ارسال کنید:", back_button("adm:stk:search"))
        return

    if data == "adm:stk:srch:cfg":
        state_set(call.from_user.id, "admin_search_by_config")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💝 متن کانفیگ (یا بخشی از آن) را ارسال کنید:", back_button("adm:stk:search"))
        return

    if data == "adm:stk:srch:name":
        state_set(call.from_user.id, "admin_search_by_name")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔮 نام سرویس (یا بخشی از آن) را ارسال کنید:", back_button("adm:stk:search"))
        return

    # ── Admin: Users ──────────────────────────────────────────────────────────
    if data == "admin:users":
        if not (admin_has_perm(uid, "view_users") or admin_has_perm(uid, "full_users") or
                any(admin_has_perm(uid, p) for p in PERM_USER_FULL)):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        _show_admin_users_list(call)
        bot.answer_callback_query(call.id)
        return

    # ── Admin: User search ────────────────────────────────────────────────────
    if data == "adm:usr:search":
        state_set(uid, "admin_user_search")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "🔍 <b>جستجوی کاربر</b>\n\n"
            "می‌توانید بر اساس موارد زیر جستجو کنید:\n"
            "• <b>آیدی عددی</b> (مثال: <code>123456789</code>)\n"
            "• <b>نام کاربری</b> (مثال: <code>@username</code>)\n"
            "• <b>نام اکانت</b> (مثال: <code>علی</code>)\n\n"
            "مقدار جستجو را ارسال کنید:",
            back_button("admin:users"))
        return

    # ── Admin: Admins management ──────────────────────────────────────────────
    if data == "admin:admins":
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "فقط اونر می‌تواند ادمین‌ها را مدیریت کند.", show_alert=True)
            return
        _show_admin_admins_panel(call)
        bot.answer_callback_query(call.id)
        return

    if data == "adm:mgr:add":
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        state_set(uid, "admin_mgr_await_id")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "➕ <b>افزودن ادمین جدید</b>\n\n"
            "آیدی عددی یا یوزرنیم کاربر مورد نظر را ارسال کنید:\n\n"
            "مثال: <code>123456789</code> یا <code>@username</code>",
            back_button("admin:admins"))
        return

    if data.startswith("adm:mgr:del:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_id = int(data.split(":")[3])
        if target_id in ADMIN_IDS:
            bot.answer_callback_query(call.id, "اونرها را نمی‌توان حذف کرد.", show_alert=True)
            return
        remove_admin_user(target_id)
        bot.answer_callback_query(call.id, "✅ ادمین حذف شد.")
        _show_admin_admins_panel(call)
        return

    if data.startswith("adm:mgr:v:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_id = int(data.split(":")[3])
        row = get_admin_user(target_id)
        user_row = get_user(target_id)
        if not row:
            bot.answer_callback_query(call.id, "ادمین یافت نشد.", show_alert=True)
            return
        perms = json.loads(row["permissions"] or "{}")
        perm_lines = "\n".join(
            f"{'✅' if perms.get(k) or perms.get('full') else '☐'} {lbl}"
            for k, lbl in ADMIN_PERMS if k != "full"
        )
        name = user_row["full_name"] if user_row else f"کاربر {target_id}"
        text = (
            f"👮 <b>اطلاعات ادمین</b>\n\n"
            f"👤 نام: {esc(name)}\n"
            f"🆔 آیدی: <code>{target_id}</code>\n"
            f"📅 افزوده شده: {esc(row['added_at'])}\n\n"
            f"🔑 <b>دسترسی‌ها:</b>\n{perm_lines}"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🗑 حذف ادمین", callback_data=f"adm:mgr:del:{target_id}"))
        kb.add(types.InlineKeyboardButton("✏️ ویرایش دسترسی‌ها", callback_data=f"adm:mgr:edit:{target_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:admins"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:mgr:edit:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        target_id = int(data.split(":")[3])
        row = get_admin_user(target_id)
        if not row:
            bot.answer_callback_query(call.id, "ادمین یافت نشد.", show_alert=True)
            return
        perms = json.loads(row["permissions"] or "{}")
        state_set(uid, "admin_mgr_select_perms", target_user_id=target_id, perms=json.dumps(perms))
        bot.answer_callback_query(call.id)
        _show_perm_selection(call, uid, target_id, perms, edit_mode=True)
        return

    if data.startswith("adm:mgr:pt:"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        perm_key = data[len("adm:mgr:pt:"):]
        sd2 = state_data(uid)
        if state_name(uid) != "admin_mgr_select_perms" or not sd2:
            bot.answer_callback_query(call.id, "جلسه منقضی شده است.", show_alert=True)
            return
        target_id = sd2.get("target_user_id")
        perms = json.loads(sd2.get("perms", "{}"))
        current = bool(perms.get(perm_key))

        if perm_key == "full":
            if not current:
                perms = {k: True for k, _ in ADMIN_PERMS}
            else:
                perms = {}
        elif perm_key == "full_users":
            if not current:
                perms["full_users"] = True
                perms["view_users"] = False
                for p in PERM_USER_FULL:
                    perms[p] = True
            else:
                perms["full_users"] = False
                for p in PERM_USER_FULL:
                    perms[p] = False
        elif perm_key == "view_users":
            if not current:
                perms["view_users"] = True
                perms["full_users"] = False
                for p in PERM_USER_FULL:
                    perms[p] = False
            else:
                perms["view_users"] = False
        else:
            perms[perm_key] = not current
            if perm_key in PERM_USER_FULL and perms.get(perm_key):
                perms["view_users"] = False
            if all(perms.get(p) for p in PERM_USER_FULL):
                perms["full_users"] = True
                perms["view_users"] = False
            if all(perms.get(k) for k, _ in ADMIN_PERMS if k != "full"):
                perms["full"] = True

        state_set(uid, "admin_mgr_select_perms",
                  target_user_id=target_id, perms=json.dumps(perms))
        bot.answer_callback_query(call.id)
        edit_mode = sd2.get("edit_mode", False)
        _show_perm_selection(call, uid, target_id, perms, edit_mode=edit_mode)
        return

    if data == "adm:mgr:confirm":
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        sd2 = state_data(uid)
        if state_name(uid) != "admin_mgr_select_perms" or not sd2:
            bot.answer_callback_query(call.id, "جلسه منقضی شده است.", show_alert=True)
            return
        target_id = sd2.get("target_user_id")
        perms = json.loads(sd2.get("perms", "{}"))
        if not any(perms.values()):
            bot.answer_callback_query(call.id, "حداقل یک سطح دسترسی انتخاب کنید.", show_alert=True)
            return
        add_admin_user(target_id, uid, perms)
        state_clear(uid)
        bot.answer_callback_query(call.id, "✅ ادمین اضافه شد.")
        try:
            bot.send_message(target_id,
                "👮 <b>شما به عنوان ادمین اضافه شدید!</b>\n\n"
                "برای دسترسی به پنل مدیریت از دستور /start استفاده کنید.")
        except Exception:
            pass
        _show_admin_admins_panel(call)
        return

    if data.startswith("adm:usr:"):
        parts     = data.split(":")
        sub       = parts[2]
        target_id = int(parts[3]) if len(parts) > 3 else 0

        if sub == "v":   # view user
            _show_admin_user_detail(call, target_id)
            bot.answer_callback_query(call.id)
            return

        if sub == "sts":  # toggle status
            user = get_user(target_id)
            new_status = "unsafe" if user["status"] == "safe" else "safe"
            set_user_status(target_id, new_status)
            label = "ناامن" if new_status == "unsafe" else "امن"
            bot.answer_callback_query(call.id, f"وضعیت کاربر به {label} تغییر کرد.")
            _show_admin_user_detail(call, target_id)
            return

        if sub == "ag":  # toggle agent
            user     = get_user(target_id)
            new_flag = 0 if user["is_agent"] else 1
            set_user_agent(target_id, new_flag)
            label = "فعال" if new_flag else "غیرفعال"
            bot.answer_callback_query(call.id, f"نمایندگی {label} شد.")
            _show_admin_user_detail(call, target_id)
            return

        if sub == "bal":  # balance menu
            user = get_user(target_id)
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("➕ افزایش", callback_data=f"adm:usr:bal+:{target_id}"),
                types.InlineKeyboardButton("➖ کاهش",  callback_data=f"adm:usr:bal-:{target_id}"),
            )
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call,
                f"💰 <b>موجودی کاربر</b>\n\n"
                f"💰 موجودی فعلی: <b>{fmt_price(user['balance'])}</b> تومان",
                kb)
            return

        if sub == "bal+":  # add balance
            state_set(uid, "admin_bal_add", target_user_id=target_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call, f"💰 مبلغی که می‌خواهید <b>اضافه</b> شود را به تومان وارد کنید:",
                         back_button(f"adm:usr:v:{target_id}"))
            return

        if sub == "bal-":  # reduce balance
            state_set(uid, "admin_bal_sub", target_user_id=target_id)
            bot.answer_callback_query(call.id)
            send_or_edit(call, f"💰 مبلغی که می‌خواهید <b>کاهش</b> یابد را به تومان وارد کنید:",
                         back_button(f"adm:usr:v:{target_id}"))
            return

        if sub == "cfgs":  # user configs
            purchases = get_user_purchases(target_id)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("➕ افزودن کانفیگ", callback_data=f"adm:usr:acfg:{target_id}"))
            if purchases:
                for p in purchases:
                    expired_mark = " ❌" if p["is_expired"] else ""
                    kb.add(types.InlineKeyboardButton(
                        f"{p['service_name']}{expired_mark}",
                        callback_data=f"adm:usrcfg:{target_id}:{p['config_id']}"
                    ))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, f"📦 کانفیگ‌های کاربر:", kb)
            return

        if sub == "acfg":  # assign config to user
            _show_admin_assign_config_type(call, target_id)
            bot.answer_callback_query(call.id)
            return

        if sub == "agp":  # agency prices list
            packs = get_packages()
            if not packs:
                bot.answer_callback_query(call.id, "پکیجی موجود نیست.", show_alert=True)
                return
            kb = types.InlineKeyboardMarkup()
            for p in packs:
                ap    = get_agency_price(target_id, p["id"])
                price = fmt_price(ap) if ap is not None else fmt_price(p["price"])
                label = f"{p['name']} | {price} ت"
                kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:usr:agpe:{target_id}:{p['id']}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
            bot.answer_callback_query(call.id)
            send_or_edit(call, "🏷 <b>قیمت‌های اختصاصی نمایندگی</b>\n\nبرای ویرایش روی پکیج بزنید:", kb)
            return

    if data.startswith("adm:usr:agpe:"):
        parts      = data.split(":")
        target_id  = int(parts[3])
        package_id = int(parts[4])
        state_set(uid, "admin_set_agency_price", target_user_id=target_id, package_id=package_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💰 قیمت اختصاصی (تومان) را وارد کنید.\nبرای بازگشت به قیمت عادی، عدد <b>0</b> بفرستید:",
                     back_button(f"adm:usr:v:{target_id}"))
        return

    # Admin user config detail (with unassign/delete)
    if data.startswith("adm:usrcfg:"):
        parts     = data.split(":")
        target_id = int(parts[2])
        config_id = int(parts[3])
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM configs WHERE id=?", (config_id,)).fetchone()
        if not row:
            bot.answer_callback_query(call.id, "یافت نشد.", show_alert=True)
            return
        text = (
            f"🔮 نام سرویس: <b>{esc(row['service_name'])}</b>\n\n"
            f"💝 Config:\n<code>{esc(row['config_text'])}</code>\n\n"
            f"🔋 Volume web: {esc(row['inquiry_link'] or '-')}\n"
            f"🗓 ثبت: {esc(row['created_at'])}\n"
            f"🗓 فروش: {esc(row['sold_at'] or '-')}"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔄 حذف از کاربر (برگشت به مانده‌ها)", callback_data=f"adm:usrcfg:unassign:{target_id}:{config_id}"))
        if not row["is_expired"]:
            kb.add(types.InlineKeyboardButton("🔴 منقضی کردن", callback_data=f"adm:stk:exp:{config_id}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:cfgs:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data.startswith("adm:usrcfg:unassign:"):
        parts     = data.split(":")
        target_id = int(parts[3])
        config_id = int(parts[4])
        with get_conn() as conn:
            # Reset config to available
            conn.execute("UPDATE configs SET sold_to=NULL, purchase_id=NULL, sold_at=NULL, reserved_payment_id=NULL, is_expired=0 WHERE id=?", (config_id,))
            # Delete the purchase record
            conn.execute("DELETE FROM purchases WHERE config_id=? AND user_id=?", (config_id, target_id))
        bot.answer_callback_query(call.id, "کانفیگ از کاربر حذف شد.")
        send_or_edit(call, "✅ کانفیگ از کاربر حذف و به مانده‌ها برگشت.", back_button(f"adm:usr:v:{target_id}"))
        return

    if data.startswith("adm:acfg:t:"):  # assign config: type selected
        parts     = data.split(":")
        target_id = int(parts[3])
        type_id   = int(parts[4])
        packs     = get_packages(type_id=type_id)
        kb        = types.InlineKeyboardMarkup()
        for p in packs:
            avail = len(get_available_configs_for_package(p["id"]))
            if avail > 0:
                kb.add(types.InlineKeyboardButton(
                    f"{p['name']} | موجود: {avail}",
                    callback_data=f"adm:acfg:p:{target_id}:{p['id']}"
                ))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📦 پکیج مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("adm:acfg:p:"):  # assign config: package selected
        parts      = data.split(":")
        target_id  = int(parts[3])
        package_id = int(parts[4])
        cfgs       = get_available_configs_for_package(package_id)
        kb         = types.InlineKeyboardMarkup()
        for c in cfgs[:50]:
            kb.add(types.InlineKeyboardButton(c["service_name"],
                                              callback_data=f"adm:acfg:do:{target_id}:{c['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔧 کانفیگ مورد نظر را انتخاب کنید:", kb)
        return

    if data.startswith("adm:acfg:do:"):  # do assign config
        parts      = data.split(":")
        target_id  = int(parts[3])
        config_id  = int(parts[4])
        with get_conn() as conn:
            cfg_row = conn.execute("SELECT * FROM configs WHERE id=?", (config_id,)).fetchone()
        if not cfg_row:
            bot.answer_callback_query(call.id, "کانفیگ یافت نشد.", show_alert=True)
            return
        purchase_id = assign_config_to_user(config_id, target_id, cfg_row["package_id"], 0, "admin_gift", is_test=0)
        bot.answer_callback_query(call.id, "کانفیگ منتقل شد!")
        send_or_edit(call, "✅ کانفیگ با موفقیت به کاربر اختصاص یافت.", back_button("admin:users"))
        try:
            deliver_purchase_message(target_id, purchase_id)
        except Exception:
            pass
        return

    # ── Admin: Broadcast ──────────────────────────────────────────────────────
    if data == "admin:broadcast":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📣 همه کاربران",  callback_data="adm:bc:all"))
        kb.add(types.InlineKeyboardButton("🛍 فقط مشتریان", callback_data="adm:bc:cust"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",       callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📣 <b>فوروارد همگانی</b>\n\nگیرنده‌ها را انتخاب کنید:", kb)
        return

    if data == "adm:bc:all":
        state_set(uid, "admin_broadcast_all")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📣 پیام خود را فوروارد یا ارسال کنید.\nبرای <b>همه کاربران</b> ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    if data == "adm:bc:cust":
        state_set(uid, "admin_broadcast_customers")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🛍 پیام خود را فوروارد یا ارسال کنید.\nفقط برای <b>مشتریان</b> ارسال می‌شود.",
                     back_button("admin:broadcast"))
        return

    # ── Admin: Settings ───────────────────────────────────────────────────────
    if data == "admin:settings":
        if not admin_has_perm(uid, "settings"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🎧 پشتیبانی",           callback_data="adm:set:support"),
            types.InlineKeyboardButton("💳 درگاه‌های پرداخت",   callback_data="adm:set:gateways"),
        )
        kb.add(types.InlineKeyboardButton("📢 کانال قفل",        callback_data="adm:set:channel"))
        kb.add(types.InlineKeyboardButton("✏️ ویرایش متن استارت", callback_data="adm:set:start_text"))
        kb.add(types.InlineKeyboardButton("🎁 تست رایگان",      callback_data="adm:set:freetest"))
        kb.add(types.InlineKeyboardButton("� قوانین خرید",      callback_data="adm:set:rules"))
        kb.add(types.InlineKeyboardButton("�💾 بکاپ",            callback_data="admin:backup"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت",        callback_data="admin:panel"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "⚙️ <b>تنظیمات</b>", kb)
        return

    if data == "adm:set:support":
        support_raw = setting_get("support_username", "")
        support_link = setting_get("support_link", "")
        support_link_desc = setting_get("support_link_desc", "")
        kb = types.InlineKeyboardMarkup()
        tg_status = "✅" if support_raw else "❌"
        link_status = "✅" if support_link else "❌"
        kb.add(types.InlineKeyboardButton(f"{tg_status} پشتیبانی تلگرام", callback_data="adm:set:support_tg"))
        kb.add(types.InlineKeyboardButton(f"{link_status} پشتیبانی آنلاین (لینک)", callback_data="adm:set:support_link"))
        kb.add(types.InlineKeyboardButton("✏️ توضیحات پشتیبانی", callback_data="adm:set:support_desc"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        text = (
            "🎧 <b>تنظیمات پشتیبانی</b>\n\n"
            f"📱 تلگرام: <code>{esc(support_raw or 'ثبت نشده')}</code>\n"
            f"🌐 لینک: <code>{esc(support_link or 'ثبت نشده')}</code>\n"
            f"📝 توضیحات: {esc(support_link_desc or 'پیش‌فرض')}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:set:support_tg":
        state_set(uid, "admin_set_support")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🎧 آیدی یا لینک پشتیبانی تلگرام را ارسال کنید.\nمثال: <code>@username</code>",
                     back_button("adm:set:support"))
        return

    if data == "adm:set:support_link":
        state_set(uid, "admin_set_support_link")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🌐 لینک پشتیبانی آنلاین را ارسال کنید.\nمثال: <code>https://example.com/chat</code>\n\nبرای حذف، <code>-</code> بفرستید.",
                     back_button("adm:set:support"))
        return

    if data == "adm:set:support_desc":
        state_set(uid, "admin_set_support_desc")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📝 توضیحات نمایشی بالای دکمه‌های پشتیبانی را بنویسید.\n\nبرای بازگشت به پیش‌فرض، <code>-</code> بفرستید.",
                     back_button("adm:set:support"))
        return

    # ── Gateway settings ──────────────────────────────────────────────────────
    if data == "adm:set:gateways":
        kb = types.InlineKeyboardMarkup()
        for gw_key, gw_label in [("card", "💳 کارت به کارت"), ("crypto", "💎 ارز دیجیتال"), ("tetrapay", "🏦 کارت به کارت آنلاین (TetraPay)"), ("swapwallet", "💎 سواپ ولت (SwapWallet)")]:
            enabled = setting_get(f"gw_{gw_key}_enabled", "0")
            status_icon = "🟢" if enabled == "1" else "🔴"
            kb.add(types.InlineKeyboardButton(f"{status_icon} {gw_label}", callback_data=f"adm:set:gw:{gw_key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💳 <b>درگاه‌های پرداخت</b>\n\nدرگاه مورد نظر را انتخاب کنید:", kb)
        return

    if data == "adm:set:gw:card":
        enabled = setting_get("gw_card_enabled", "0")
        vis = setting_get("gw_card_visibility", "public")
        card = setting_get("payment_card", "")
        bank = setting_get("payment_bank", "")
        owner = setting_get("payment_owner", "")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:card:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:card:vis"),
        )
        kb.add(types.InlineKeyboardButton("💳 شماره کارت", callback_data="adm:set:card"))
        kb.add(types.InlineKeyboardButton("🏦 نام بانک", callback_data="adm:set:bank"))
        kb.add(types.InlineKeyboardButton("👤 نام صاحب کارت", callback_data="adm:set:owner"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        text = (
            "💳 <b>درگاه کارت به کارت</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"کارت: <code>{esc(card or 'ثبت نشده')}</code>\n"
            f"بانک: {esc(bank or 'ثبت نشده')}\n"
            f"صاحب: {esc(owner or 'ثبت نشده')}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:card:toggle":
        enabled = setting_get("gw_card_enabled", "0")
        setting_set("gw_card_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:card")
        return

    if data == "adm:gw:card:vis":
        vis = setting_get("gw_card_visibility", "public")
        setting_set("gw_card_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:card")
        return

    if data == "adm:set:gw:crypto":
        enabled = setting_get("gw_crypto_enabled", "0")
        vis = setting_get("gw_crypto_visibility", "public")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:crypto:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:crypto:vis"),
        )
        for coin_key, coin_label in CRYPTO_COINS:
            addr = setting_get(f"crypto_{coin_key}", "")
            status_icon = "✅" if addr else "❌"
            kb.add(types.InlineKeyboardButton(f"{status_icon} {coin_label}", callback_data=f"adm:set:cw:{coin_key}"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        text = (
            "💎 <b>درگاه ارز دیجیتال</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            "برای ویرایش آدرس ولت روی هر ارز بزنید:"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:crypto:toggle":
        enabled = setting_get("gw_crypto_enabled", "0")
        setting_set("gw_crypto_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:crypto")
        return

    if data == "adm:gw:crypto:vis":
        vis = setting_get("gw_crypto_visibility", "public")
        setting_set("gw_crypto_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:crypto")
        return

    if data == "adm:set:gw:tetrapay":
        enabled = setting_get("gw_tetrapay_enabled", "0")
        vis = setting_get("gw_tetrapay_visibility", "public")
        api_key = setting_get("tetrapay_api_key", "")
        mode_bot = setting_get("tetrapay_mode_bot", "1")
        mode_web = setting_get("tetrapay_mode_web", "1")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        bot_label = "🟢 فعال" if mode_bot == "1" else "🔴 غیرفعال"
        web_label = "🟢 فعال" if mode_web == "1" else "🔴 غیرفعال"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:tetrapay:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}", callback_data="adm:gw:tetrapay:vis"),
        )
        kb.row(
            types.InlineKeyboardButton(f"تلگرام: {bot_label}", callback_data="adm:gw:tetrapay:mode_bot"),
            types.InlineKeyboardButton(f"مرورگر: {web_label}", callback_data="adm:gw:tetrapay:mode_web"),
        )
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API", callback_data="adm:set:tetrapay_key"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سایت TetraPay", url="https://tetra98.com"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        if api_key:
            key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>"
        else:
            key_display = "❌ <b>ثبت نشده</b> — ابتدا از سایت TetraPay کلید API خود را دریافت کنید"
        text = (
            "🏦 <b>درگاه کارت به کارت آنلاین (TetraPay)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"💳 پرداخت از تلگرام: {bot_label}\n"
            f"🌐 پرداخت از مرورگر: {web_label}\n\n"
            f"کلید API: {key_display}"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:tetrapay:toggle":
        enabled = setting_get("gw_tetrapay_enabled", "0")
        setting_set("gw_tetrapay_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:vis":
        vis = setting_get("gw_tetrapay_visibility", "public")
        setting_set("gw_tetrapay_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:mode_bot":
        cur = setting_get("tetrapay_mode_bot", "1")
        setting_set("tetrapay_mode_bot", "0" if cur == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:gw:tetrapay:mode_web":
        cur = setting_get("tetrapay_mode_web", "1")
        setting_set("tetrapay_mode_web", "0" if cur == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:tetrapay")
        return

    if data == "adm:set:tetrapay_key":
        state_set(uid, "admin_set_tetrapay_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API تتراپی را ارسال کنید:", back_button("adm:set:gw:tetrapay"))
        return

    if data == "adm:set:gw:swapwallet":
        enabled  = setting_get("gw_swapwallet_enabled", "0")
        vis      = setting_get("gw_swapwallet_visibility", "public")
        api_key  = setting_get("swapwallet_api_key", "")
        username = setting_get("swapwallet_username", "")
        enabled_label = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
        vis_label     = "👥 عمومی" if vis == "public" else "🔒 کاربران امن"
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"وضعیت: {enabled_label}", callback_data="adm:gw:swapwallet:toggle"),
            types.InlineKeyboardButton(f"نمایش: {vis_label}",    callback_data="adm:gw:swapwallet:vis"),
        )
        kb.add(types.InlineKeyboardButton("🔑 تنظیم کلید API",             callback_data="adm:set:swapwallet_key"))
        kb.add(types.InlineKeyboardButton("👤 نام کاربری فروشگاه",          callback_data="adm:set:swapwallet_username"))
        if not api_key:
            kb.add(types.InlineKeyboardButton("🌐 دریافت کلید API از سواپ ولت", url="https://swapwallet.app"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:set:gateways"))
        key_display = f"<code>{esc(api_key[:8])}...{esc(api_key[-4:])}</code>" if api_key else "❌ <b>ثبت نشده</b>"
        text = (
            "💎 <b>درگاه سواپ ولت (SwapWallet)</b>\n\n"
            f"وضعیت: {enabled_label}\n"
            f"نمایش: {vis_label}\n\n"
            f"👤 نام کاربری فروشگاه: <code>{esc(username or 'ثبت نشده')}</code>\n"
            f"🔑 کلید API: {key_display}\n\n"
            "📖 <b>راهنمای دریافت کلید API:</b>\n"
            "۱. وارد اپلیکیشن <a href='https://swapwallet.app'>سواپ ولت</a> شوید\n"
            "۲. پروفایل ← کلید API\n"
            "۳. روی «ایجاد کلید جدید» کلیک کنید\n"
            "۴. کلید را با فرمت <code>apikey-xxx</code> کپی کنید\n\n"
            "ربات: @SwapWalletBot | سایت: swapwallet.app"
        )
        bot.answer_callback_query(call.id)
        send_or_edit(call, text, kb)
        return

    if data == "adm:gw:swapwallet:toggle":
        enabled = setting_get("gw_swapwallet_enabled", "0")
        setting_set("gw_swapwallet_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet")
        return

    if data == "adm:gw:swapwallet:vis":
        vis = setting_get("gw_swapwallet_visibility", "public")
        setting_set("gw_swapwallet_visibility", "secure" if vis == "public" else "public")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:gw:swapwallet")
        return

    if data == "adm:set:swapwallet_key":
        state_set(uid, "admin_set_swapwallet_key")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🔑 کلید API سواپ ولت را ارسال کنید (مثال: <code>apikey-xxx...</code>):", back_button("adm:set:gw:swapwallet"))
        return

    if data == "adm:set:swapwallet_username":
        state_set(uid, "admin_set_swapwallet_username")
        bot.answer_callback_query(call.id)
        current = setting_get("swapwallet_username", "")
        send_or_edit(call,
            f"👤 نام کاربری فروشگاه سواپ ولت را ارسال کنید.\n"
            f"مقدار فعلی: <code>{esc(current or 'ثبت نشده')}</code>",
            back_button("adm:set:gw:swapwallet"))
        return

    if data == "adm:set:payment":
        _fake_call(call, "adm:set:gw:card")
        bot.answer_callback_query(call.id)
        return

    if data == "adm:set:cardvis":
        _fake_call(call, "adm:gw:card:vis")
        bot.answer_callback_query(call.id)
        return

    if data == "adm:set:card":
        state_set(uid, "admin_set_card")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "💳 شماره کارت را ارسال کنید:", back_button("adm:set:gw:card"))
        return

    if data == "adm:set:bank":
        state_set(uid, "admin_set_bank")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "🏦 نام بانک را ارسال کنید:", back_button("adm:set:gw:card"))
        return

    if data == "adm:set:owner":
        state_set(uid, "admin_set_owner")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "👤 نام و نام خانوادگی صاحب کارت را ارسال کنید:", back_button("adm:set:gw:card"))
        return

    if data == "adm:set:crypto":
        _fake_call(call, "adm:set:gw:crypto")
        bot.answer_callback_query(call.id)
        return

    if data.startswith("adm:set:cw:"):
        coin_key   = data.split(":")[3]
        coin_label = next((l for k, l in CRYPTO_COINS if k == coin_key), coin_key)
        state_set(uid, "admin_set_crypto_wallet", coin_key=coin_key)
        bot.answer_callback_query(call.id)
        current    = setting_get(f"crypto_{coin_key}", "")
        send_or_edit(
            call,
            f"💎 آدرس ولت <b>{coin_label}</b> را وارد کنید.\n"
            f"آدرس فعلی: <code>{esc(current or 'ثبت نشده')}</code>\n\n"
            "برای حذف، عدد <code>-</code> بفرستید.",
            back_button("adm:set:gw:crypto")
        )
        return

    if data == "adm:set:channel":
        current = setting_get("channel_id", "")
        state_set(uid, "admin_set_channel")
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            f"📢 <b>کانال قفل</b>\n\n"
            f"کانال فعلی: {esc(current or 'ثبت نشده')}\n\n"
            "@username کانال را وارد کنید\n"
            "برای غیرفعال کردن، <code>-</code> بفرستید\n\n"
            "⚠️ ربات باید ادمین کانال باشد",
            back_button("admin:settings")
        )
        return

    if data == "adm:set:start_text":
        current = setting_get("start_text", "")
        state_set(uid, "admin_set_start_text")
        bot.answer_callback_query(call.id)
        preview = esc(current[:200]) + "..." if len(current) > 200 else esc(current or "پیش‌فرض")
        send_or_edit(
            call,
            f"✏️ <b>ویرایش متن استارت</b>\n\n"
            f"متن فعلی:\n{preview}\n\n"
            "متن جدید را ارسال کنید. می‌توانید از تگ‌های HTML استفاده کنید.\n"
            "برای بازگشت به متن پیش‌فرض، <code>-</code> بفرستید.",
            back_button("admin:settings")
        )
        return

    # ── Admin: Free Test Settings ─────────────────────────────────────────────
    if data == "adm:set:freetest":
        enabled = setting_get("free_test_enabled", "1")
        agent_limit = setting_get("agent_test_limit", "0")
        agent_period = setting_get("agent_test_period", "day")
        period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
        kb = types.InlineKeyboardMarkup()
        toggle_label = "🔴 غیرفعال کردن" if enabled == "1" else "🟢 فعال کردن"
        kb.add(types.InlineKeyboardButton(toggle_label, callback_data="adm:ft:toggle"))
        kb.add(types.InlineKeyboardButton("🔄 ریست تست رایگان همه کاربران", callback_data="adm:ft:reset"))
        kb.add(types.InlineKeyboardButton(f"🤝 تعداد تست همکاران: {agent_limit} در {period_labels.get(agent_period, agent_period)}", callback_data="adm:ft:agent"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            f"🎁 <b>تنظیمات تست رایگان</b>\n\n"
            f"وضعیت: {'🟢 فعال' if enabled == '1' else '🔴 غیرفعال'}\n"
            f"تست همکاران: <b>{agent_limit}</b> عدد در {period_labels.get(agent_period, agent_period)}",
            kb
        )
        return

    if data == "adm:ft:toggle":
        enabled = setting_get("free_test_enabled", "1")
        setting_set("free_test_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:freetest")
        return

    if data == "adm:ft:reset":
        reset_all_free_tests()
        bot.answer_callback_query(call.id, "✅ تست رایگان همه کاربران ریست شد.", show_alert=True)
        _fake_call(call, "adm:set:freetest")
        return

    if data == "adm:ft:agent":
        state_set(uid, "admin_set_agent_test_limit")
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            "🤝 <b>تعداد تست همکاران</b>\n\n"
            "تعداد تست رایگان همکاران را وارد کنید.\n"
            "فرمت: <code>تعداد بازه</code>\n\n"
            "مثال:\n"
            "<code>5 day</code> → ۵ تست در روز\n"
            "<code>10 week</code> → ۱۰ تست در هفته\n"
            "<code>20 month</code> → ۲۰ تست در ماه\n\n"
            "برای غیرفعال کردن محدودیت، <code>0</code> بفرستید.",
            back_button("adm:set:freetest")
        )
        return

    # ── Admin: Purchase Rules ─────────────────────────────────────────────────
    if data == "adm:set:rules":
        enabled = setting_get("purchase_rules_enabled", "0")
        kb = types.InlineKeyboardMarkup()
        toggle_label = "🔴 غیرفعال کردن" if enabled == "1" else "🟢 فعال کردن"
        kb.add(types.InlineKeyboardButton(toggle_label, callback_data="adm:rules:toggle"))
        kb.add(types.InlineKeyboardButton("✏️ ویرایش متن قوانین", callback_data="adm:rules:edit"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            f"📜 <b>قوانین خرید</b>\n\n"
            f"وضعیت: {'🟢 فعال' if enabled == '1' else '🔴 غیرفعال'}\n\n"
            "وقتی فعال باشد، کاربر قبل از اولین خرید باید قوانین را بپذیرد.", kb)
        return

    if data == "adm:rules:toggle":
        enabled = setting_get("purchase_rules_enabled", "0")
        setting_set("purchase_rules_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "adm:set:rules")
        return

    if data == "adm:rules:edit":
        state_set(uid, "admin_edit_rules_text")
        bot.answer_callback_query(call.id)
        current_text = setting_get("purchase_rules_text", "")
        preview = f"\n\n📝 متن فعلی:\n{esc(current_text[:200])}..." if len(current_text) > 200 else (f"\n\n📝 متن فعلی:\n{esc(current_text)}" if current_text else "")
        send_or_edit(call,
            f"✏️ <b>ویرایش متن قوانین خرید</b>{preview}\n\n"
            "متن جدید قوانین خرید را ارسال کنید:",
            back_button("adm:set:rules"))
        return

    if data == "buy:accept_rules":
        # User accepted rules, mark and proceed to buy
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"rules_accepted_{uid}", "1")
            )
        bot.answer_callback_query(call.id)
        # Proceed to buy flow
        _fake_call(call, "buy:start_real")
        return

    # ── Admin: Backup ─────────────────────────────────────────────────────────
    if data == "admin:backup":
        enabled  = setting_get("backup_enabled", "0")
        interval = setting_get("backup_interval", "24")
        target   = setting_get("backup_target_id", "")
        kb       = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💾 بکاپ دستی", callback_data="adm:bkp:manual"))
        kb.add(types.InlineKeyboardButton("📥 بازیابی بکاپ", callback_data="adm:bkp:restore"))
        toggle_label = "🔴 غیرفعال کردن بکاپ خودکار" if enabled == "1" else "🟢 فعال کردن بکاپ خودکار"
        kb.add(types.InlineKeyboardButton(toggle_label, callback_data="adm:bkp:toggle"))
        kb.add(types.InlineKeyboardButton(f"⏰ زمان‌بندی: هر {interval} ساعت", callback_data="adm:bkp:interval"))
        kb.add(types.InlineKeyboardButton("📤 تنظیم مقصد", callback_data="adm:bkp:target"))
        kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:settings"))
        bot.answer_callback_query(call.id)
        send_or_edit(
            call,
            f"💾 <b>بکاپ</b>\n\n"
            f"بکاپ خودکار: {'🟢 فعال' if enabled == '1' else '🔴 غیرفعال'}\n"
            f"هر {interval} ساعت\n"
            f"مقصد: <code>{esc(target or 'ثبت نشده')}</code>",
            kb
        )
        return

    if data == "adm:bkp:manual":
        bot.answer_callback_query(call.id)
        _send_backup(uid)
        return

    if data == "adm:bkp:toggle":
        enabled = setting_get("backup_enabled", "0")
        setting_set("backup_enabled", "0" if enabled == "1" else "1")
        bot.answer_callback_query(call.id, "تغییر یافت.")
        _fake_call(call, "admin:backup")
        return

    if data == "adm:bkp:interval":
        state_set(uid, "admin_set_backup_interval")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "⏰ بازه بکاپ خودکار را به ساعت وارد کنید (مثال: 6، 12، 24):",
                     back_button("admin:backup"))
        return

    if data == "adm:bkp:target":
        state_set(uid, "admin_set_backup_target")
        bot.answer_callback_query(call.id)
        send_or_edit(call, "📤 آیدی عددی کاربر یا کانال برای دریافت بکاپ را وارد کنید:",
                     back_button("admin:backup"))
        return

    if data == "adm:bkp:restore":
        state_set(uid, "admin_restore_backup")
        bot.answer_callback_query(call.id)
        send_or_edit(call,
            "📥 <b>بازیابی بکاپ</b>\n\n"
            "⚠️ <b>توجه:</b> با بازیابی بکاپ، دیتابیس فعلی ربات حذف و با فایل بکاپ جایگزین می‌شود.\n\n"
            "فایل بکاپ (<code>.db</code>) را ارسال کنید:",
            back_button("admin:backup"))
        return

    # ── Admin: Payment approve/reject ─────────────────────────────────────────
    if data.startswith("adm:pay:ap:"):
        if not admin_has_perm(uid, "approve_payments"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        payment_id = int(data.split(":")[3])
        state_set(uid, "admin_payment_approve_note", payment_id=payment_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, "✅ متن تأیید را برای کاربر ارسال کنید:", back_button("admin:panel"))
        return

    if data.startswith("adm:pay:rj:"):
        if not admin_has_perm(uid, "approve_payments"):
            bot.answer_callback_query(call.id, "دسترسی مجاز نیست.", show_alert=True)
            return
        payment_id = int(data.split(":")[3])
        state_set(uid, "admin_payment_reject_note", payment_id=payment_id)
        bot.answer_callback_query(call.id)
        send_or_edit(call, "❌ متن رد را برای کاربر ارسال کنید:", back_button("admin:panel"))
        return

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

# ── Admin helper renderers ─────────────────────────────────────────────────────
def _show_admin_types(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ افزودن نوع جدید", callback_data="admin:type:add"))
    all_types = get_all_types()
    for item in all_types:
        kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data="noop"))
        kb.row(
            types.InlineKeyboardButton("✏️ ویرایش", callback_data=f"admin:type:edit:{item['id']}"),
            types.InlineKeyboardButton("🗑 حذف",  callback_data=f"admin:type:del:{item['id']}"),
        )
        kb.add(types.InlineKeyboardButton(f"➕ افزودن پکیج برای {item['name']}", callback_data=f"admin:pkg:add:t:{item['id']}"))
        packs = get_packages(type_id=item['id'], include_inactive=False)
        for p in packs:
            kb.row(
                types.InlineKeyboardButton(f"📦 {p['name']} | {p['volume_gb']}GB | {fmt_price(p['price'])}ت",
                                           callback_data="noop"),
                types.InlineKeyboardButton("✏️", callback_data=f"admin:pkg:edit:{p['id']}"),
                types.InlineKeyboardButton("🗑",  callback_data=f"admin:pkg:del:{p['id']}"),
            )
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(call, "🧩 <b>مدیریت نوع و پکیج‌ها</b>", kb)

def _show_admin_stock(call):
    rows = get_registered_packages_stock()
    kb   = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📝 ثبت کانفیگ", callback_data="admin:add_config"))
    kb.add(types.InlineKeyboardButton("🔍 جستجو", callback_data="adm:stk:search"))
    total_avail  = sum(r["stock"] for r in rows)
    total_sold   = sum(r["sold_count"] for r in rows)
    total_expired = sum(r["expired_count"] for r in rows)
    kb.row(
        types.InlineKeyboardButton(f"🟢 کل موجود ({total_avail})",  callback_data="adm:stk:all:av:0"),
        types.InlineKeyboardButton(f"🔴 کل فروخته ({total_sold})", callback_data="adm:stk:all:sl:0"),
        types.InlineKeyboardButton(f"❌ کل منقضی ({total_expired})", callback_data="adm:stk:all:ex:0"),
    )
    for row in rows:
        kb.add(types.InlineKeyboardButton(
            f"📦 {row['type_name']} - {row['name']} | 🟢{row['stock']} 🔴{row['sold_count']} ❌{row['expired_count']}",
            callback_data=f"adm:stk:pk:{row['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(call, "📁 <b>کانفیگ‌ها</b>", kb)

def _show_admin_users_list(call):
    rows  = get_users()
    total = count_all_users()
    kb    = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="adm:usr:search"))
    for row in rows[:100]:
        status_icon = "🔘" if row["status"] == "safe" else "⚠️"
        agent_icon  = "🤝" if row["is_agent"] else ""
        label = f"{status_icon}{agent_icon} {row['full_name']} | {display_username(row['username'])}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:usr:v:{row['user_id']}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(call, f"👥 <b>مدیریت کاربران</b>\n\n👤 تعداد کل کاربران: <b>{total}</b> نفر", kb)

def _show_admin_user_detail(call, user_id):
    row = get_user_detail(user_id)
    if not row:
        send_or_edit(call, "کاربر یافت نشد.", back_button("admin:users"))
        return
    status_label = "🔘 امن" if row["status"] == "safe" else "⚠️ ناامن"
    agent_label  = "🤝 نمایندگی فعال" if row["is_agent"] else "❌ نمایندگی غیرفعال"
    text = (
        "👤 <b>اطلاعات کاربر</b>\n\n"
        f"📱 نام: {esc(row['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(row['username']))}\n"
        f"🔢 آیدی: <code>{row['user_id']}</code>\n"
        f"💰 موجودی: <b>{fmt_price(row['balance'])}</b> تومان\n"
        f"🛍 تعداد خرید: <b>{row['purchase_count']}</b>\n"
        f"💵 مجموع خرید: <b>{fmt_price(row['total_spent'])}</b> تومان\n"
        f"🕒 عضویت: {esc(row['joined_at'])}\n"
        f"وضعیت: {status_label}\n"
        f"نمایندگی: {agent_label}"
    )
    uid_t = row["user_id"]
    kb    = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(f"🔄 {status_label}",    callback_data=f"adm:usr:sts:{uid_t}"),
        types.InlineKeyboardButton(f"🤝 نمایندگی",          callback_data=f"adm:usr:ag:{uid_t}"),
    )
    kb.add(types.InlineKeyboardButton("💰 موجودی",           callback_data=f"adm:usr:bal:{uid_t}"))
    kb.add(types.InlineKeyboardButton("📦 کانفیگ‌ها",         callback_data=f"adm:usr:cfgs:{uid_t}"))
    if row["is_agent"]:
        kb.add(types.InlineKeyboardButton("🏷 قیمت‌های نمایندگی", callback_data=f"adm:usr:agp:{uid_t}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:users"))
    send_or_edit(call, text, kb)

def _show_admin_user_detail_msg(chat_id, user_id):
    """Send user detail as a new message (for use from message handlers)."""
    row = get_user_detail(user_id)
    if not row:
        bot.send_message(chat_id, "کاربر یافت نشد.", reply_markup=back_button("admin:users"))
        return
    status_label = "🔘 امن" if row["status"] == "safe" else "⚠️ ناامن"
    agent_label  = "🤝 نمایندگی فعال" if row["is_agent"] else "❌ نمایندگی غیرفعال"
    text = (
        "👤 <b>اطلاعات کاربر</b>\n\n"
        f"📱 نام: {esc(row['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(row['username']))}\n"
        f"🔢 آیدی: <code>{row['user_id']}</code>\n"
        f"💰 موجودی: <b>{fmt_price(row['balance'])}</b> تومان\n"
        f"🛍 تعداد خرید: <b>{row['purchase_count']}</b>\n"
        f"💵 مجموع خرید: <b>{fmt_price(row['total_spent'])}</b> تومان\n"
        f"🕒 عضویت: {esc(row['joined_at'])}\n"
        f"وضعیت: {status_label}\n"
        f"نمایندگی: {agent_label}"
    )
    uid_t = row["user_id"]
    kb    = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(f"🔄 {status_label}",    callback_data=f"adm:usr:sts:{uid_t}"),
        types.InlineKeyboardButton(f"🤝 نمایندگی",          callback_data=f"adm:usr:ag:{uid_t}"),
    )
    kb.add(types.InlineKeyboardButton("💰 موجودی",           callback_data=f"adm:usr:bal:{uid_t}"))
    kb.add(types.InlineKeyboardButton("📦 کانفیگ‌ها",         callback_data=f"adm:usr:cfgs:{uid_t}"))
    if row["is_agent"]:
        kb.add(types.InlineKeyboardButton("🏷 قیمت‌های نمایندگی", callback_data=f"adm:usr:agp:{uid_t}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:users"))
    bot.send_message(chat_id, text, reply_markup=kb)

def _show_admin_assign_config_type(call, target_id):
    items = get_all_types()
    kb    = types.InlineKeyboardMarkup()
    for item in items:
        kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data=f"adm:acfg:t:{target_id}:{item['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
    send_or_edit(call, "📝 نوع کانفیگ را انتخاب کنید:", kb)

def _fake_call(call, new_data):
    """Re-dispatch a callback with different data (for re-rendering pages)."""
    class _FakeCall:
        def __init__(self, original, data):
            self.from_user = original.from_user
            self.message   = original.message
            self.data      = data
            self.id        = original.id
    _dispatch_callback(_FakeCall(call, new_data), call.from_user.id, new_data)

# ── Backup ─────────────────────────────────────────────────────────────────────
def _send_backup(target_chat_id):
    try:
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        with open(DB_NAME, "rb") as f:
            bot.send_document(
                target_chat_id, f,
                caption=f"🗄 بکاپ دیتابیس\n\n📦 ConfigFlow_backup_{ts}.db",
                visible_file_name=f"ConfigFlow_backup_{ts}.db"
            )
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
        # Sleep for interval hours (check every minute for setting changes)
        for _ in range(interval * 60):
            import time; time.sleep(60)
            # Re-read interval in case it changed
            new_interval = int(setting_get("backup_interval", "24") or "24")
            if new_interval != interval:
                break

# ── Message handler ────────────────────────────────────────────────────────────
@bot.message_handler(content_types=["text", "photo", "document"])
def universal_handler(message):
    uid    = message.from_user.id
    ensure_user(message.from_user)

    # Channel check
    if not check_channel_membership(uid):
        channel_lock_message(message)
        return

    sn = state_name(uid)
    sd = state_data(uid)

    try:
        # ── Broadcast ─────────────────────────────────────────────────────────
        if sn == "admin_broadcast_all" and is_admin(uid):
            users = get_users()
            sent  = 0
            for u in users:
                try:
                    bot.copy_message(u["user_id"], message.chat.id, message.message_id)
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} کاربر ارسال شد.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_broadcast_customers" and is_admin(uid):
            users = get_users(has_purchase=True)
            sent  = 0
            for u in users:
                try:
                    bot.copy_message(u["user_id"], message.chat.id, message.message_id)
                    sent += 1
                except Exception:
                    pass
            state_clear(uid)
            bot.send_message(uid, f"✅ پیام برای {sent} مشتری ارسال شد.", reply_markup=kb_admin_panel())
            return

        # ── Wallet amount ──────────────────────────────────────────────────────
        if sn == "await_wallet_amount":
            amount = parse_int(message.text or "")
            if not amount or amount <= 0:
                bot.send_message(uid, "⚠️ لطفاً مبلغ معتبر وارد کنید.", reply_markup=back_button("main"))
                return
            state_set(uid, "wallet_charge_method", amount=amount)
            kb = types.InlineKeyboardMarkup()
            if is_gateway_available("card", uid) and is_card_info_complete():
                kb.add(types.InlineKeyboardButton("💳 کارت به کارت",  callback_data="wallet:charge:card"))
            if is_gateway_available("crypto", uid):
                kb.add(types.InlineKeyboardButton("💎 ارز دیجیتال",       callback_data="wallet:charge:crypto"))
            if is_gateway_available("tetrapay", uid):
                kb.add(types.InlineKeyboardButton("🏦 پرداخت آنلاین (TetraPay)", callback_data="wallet:charge:tetrapay"))
            if is_gateway_available("swapwallet", uid):
                kb.add(types.InlineKeyboardButton("💎 پرداخت با سواپ ولت", callback_data="wallet:charge:swapwallet"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت",            callback_data="nav:main"))
            bot.send_message(
                uid,
                f"💰 مبلغ <b>{fmt_price(amount)}</b> تومان ثبت شد.\nروش پرداخت را انتخاب کنید:",
                reply_markup=kb
            )
            return

        # ── Wallet receipt ─────────────────────────────────────────────────────
        if sn == "await_wallet_receipt":
            payment_id  = sd.get("payment_id")
            file_id     = None
            text_value  = message.text or ""
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
            update_payment_receipt(payment_id, file_id, text_value.strip())
            state_clear(uid)
            bot.send_message(uid, "✅ رسید شما دریافت شد و برای بررسی ادمین ارسال گردید.",
                             reply_markup=kb_main(uid))
            send_payment_to_admins(payment_id)
            return

        # ── Purchase receipt ───────────────────────────────────────────────────
        if sn == "await_purchase_receipt":
            payment_id  = sd.get("payment_id")
            file_id     = None
            text_value  = message.text or ""
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
            update_payment_receipt(payment_id, file_id, text_value.strip())
            state_clear(uid)
            bot.send_message(uid, "✅ رسید شما دریافت شد و برای بررسی ادمین ارسال گردید.",
                             reply_markup=kb_main(uid))
            send_payment_to_admins(payment_id)
            return

        # ── Renewal receipt ────────────────────────────────────────────────────
        if sn == "await_renewal_receipt":
            payment_id  = sd.get("payment_id")
            file_id     = None
            text_value  = message.text or ""
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
            update_payment_receipt(payment_id, file_id, text_value.strip())
            state_clear(uid)
            bot.send_message(uid, "✅ رسید شما دریافت شد و برای بررسی ادمین ارسال گردید.",
                             reply_markup=kb_main(uid))
            send_payment_to_admins(payment_id)
            return

        # ── Admin: Type add/edit ───────────────────────────────────────────────
        if sn == "admin_add_type" and is_admin(uid):
            name = (message.text or "").strip()
            if not name:
                bot.send_message(uid, "⚠️ نام نوع نمی‌تواند خالی باشد.", reply_markup=back_button("admin:types"))
                return
            state_set(uid, "admin_add_type_desc", type_name=name)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ توضیحاتی نمی‌خواهم وارد کنم", callback_data="admin:type:skipdesc"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:types"))
            bot.send_message(uid,
                f"📝 توضیحات نوع <b>{esc(name)}</b> را وارد کنید:\n\n"
                "این توضیحات پس از ارسال کانفیگ به کاربر نمایش داده می‌شود.\n"
                "اگر نمی‌خواهید توضیحاتی وارد کنید، دکمه زیر را بزنید:", reply_markup=kb)
            return

        if sn == "admin_add_type_desc" and is_admin(uid):
            desc = (message.text or "").strip()
            name = sd["type_name"]
            try:
                add_type(name, desc)
                state_clear(uid)
                bot.send_message(uid, "✅ نوع جدید ثبت شد.", reply_markup=kb_admin_panel())
            except sqlite3.IntegrityError:
                state_clear(uid)
                bot.send_message(uid, "⚠️ این نوع قبلاً ثبت شده است.", reply_markup=back_button("admin:types"))
            return

        if sn == "admin_edit_type" and is_admin(uid):
            new_name = (message.text or "").strip()
            if not new_name:
                bot.send_message(uid, "⚠️ نام معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            update_type(sd["type_id"], new_name)
            state_clear(uid)
            bot.send_message(uid, "✅ نوع با موفقیت ویرایش شد.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_edit_type_desc" and is_admin(uid):
            desc = (message.text or "").strip()
            update_type_description(sd["type_id"], desc)
            state_clear(uid)
            bot.send_message(uid, "✅ توضیحات نوع با موفقیت ویرایش شد.", reply_markup=kb_admin_panel())
            return

        # ── Admin: Package add ─────────────────────────────────────────────────
        if sn == "admin_add_package_name" and is_admin(uid):
            name = (message.text or "").strip()
            if not name:
                bot.send_message(uid, "⚠️ نام پکیج معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            state_set(uid, "admin_add_package_volume", type_id=sd["type_id"], package_name=name)
            bot.send_message(uid, "🔋 حجم پکیج را به گیگ وارد کنید:", reply_markup=back_button("admin:types"))
            return

        if sn == "admin_add_package_volume" and is_admin(uid):
            volume = parse_int(message.text or "")
            if volume is None or volume < 0:
                bot.send_message(uid, "⚠️ حجم معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            state_set(uid, "admin_add_package_duration",
                      type_id=sd["type_id"], package_name=sd["package_name"], volume=volume)
            bot.send_message(uid, "⏰ مدت پکیج را به روز وارد کنید:", reply_markup=back_button("admin:types"))
            return

        if sn == "admin_add_package_duration" and is_admin(uid):
            duration = parse_int(message.text or "")
            if duration is None or duration < 0:
                bot.send_message(uid, "⚠️ مدت معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            state_set(uid, "admin_add_package_price",
                      type_id=sd["type_id"], package_name=sd["package_name"],
                      volume=sd["volume"], duration=duration)
            bot.send_message(uid, "💰 قیمت پکیج را به تومان وارد کنید.\nبرای تست رایگان عدد <b>0</b> بفرستید:",
                             reply_markup=back_button("admin:types"))
            return

        if sn == "admin_add_package_price" and is_admin(uid):
            price = parse_int(message.text or "")
            if price is None or price < 0:
                bot.send_message(uid, "⚠️ قیمت معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                return
            add_package(sd["type_id"], sd["package_name"], sd["volume"], sd["duration"], price)
            state_clear(uid)
            bot.send_message(uid, "✅ پکیج با موفقیت ثبت شد.", reply_markup=kb_admin_panel())
            return

        # ── Admin: Package edit field ──────────────────────────────────────────
        if sn == "admin_edit_pkg_field" and is_admin(uid):
            field_key  = sd["field_key"]
            package_id = sd["package_id"]
            db_field_map = {"name": "name", "price": "price", "volume": "volume_gb", "dur": "duration_days", "position": "position"}
            db_field   = db_field_map.get(field_key)
            raw        = (message.text or "").strip()
            if field_key == "name":
                if not raw:
                    bot.send_message(uid, "⚠️ نام معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                    return
                update_package_field(package_id, db_field, raw)
            else:
                val = parse_int(raw)
                if val is None or (field_key != "position" and val < 0) or (field_key == "position" and val < 1):
                    bot.send_message(uid, "⚠️ مقدار عددی معتبر وارد کنید.", reply_markup=back_button("admin:types"))
                    return
                update_package_field(package_id, db_field, val)
            state_clear(uid)
            bot.send_message(uid, "✅ پکیج با موفقیت ویرایش شد.", reply_markup=kb_admin_panel())
            return

        # ── Admin: Config add ──────────────────────────────────────────────────
        if sn == "admin_add_config_service" and is_admin(uid):
            service_name = (message.text or "").strip()
            if not service_name:
                bot.send_message(uid, "⚠️ نام سرویس را وارد کنید.", reply_markup=back_button("admin:add_config"))
                return
            state_set(uid, "admin_add_config_text",
                      package_id=sd["package_id"], type_id=sd["type_id"], service_name=service_name)
            bot.send_message(uid, "💝 متن کانفیگ را ارسال کنید:", reply_markup=back_button("admin:add_config"))
            return

        if sn == "admin_add_config_text" and is_admin(uid):
            config_text = (message.text or "").strip()
            if not config_text:
                bot.send_message(uid, "⚠️ متن کانفیگ را وارد کنید.", reply_markup=back_button("admin:add_config"))
                return
            state_set(uid, "admin_add_config_link",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      service_name=sd["service_name"], config_text=config_text)
            bot.send_message(uid, "🔗 لینک استعلام را ارسال کنید.\nاگر ندارید، <code>-</code> بفرستید.",
                             reply_markup=back_button("admin:add_config"))
            return

        if sn == "admin_add_config_link" and is_admin(uid):
            inquiry_link = (message.text or "").strip()
            if inquiry_link == "-":
                inquiry_link = ""
            add_config(sd["type_id"], sd["package_id"], sd["service_name"], sd["config_text"], inquiry_link)
            state_clear(uid)
            bot.send_message(uid, "✅ کانفیگ با موفقیت ثبت شد.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_add_config_bulk" and is_admin(uid):
            # Legacy fallback — should not reach here with new flow
            state_clear(uid)
            bot.send_message(uid, "⚠️ لطفاً دوباره از منو اقدام کنید.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_bulk_count" and is_admin(uid):
            count = parse_int(message.text or "")
            if not count or count <= 0:
                bot.send_message(uid, "⚠️ تعداد معتبر وارد کنید.", reply_markup=back_button("admin:add_config"))
                return
            pkg_id = sd["package_id"]
            state_set(uid, "admin_bulk_prefix",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      has_inquiry=sd["has_inquiry"], count=count)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ بعدی (بدون پیشوند)", callback_data=f"adm:cfg:bulk:skippre:{pkg_id}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
            bot.send_message(uid,
                "✂️ <b>پیشوند حذفی از نام کانفیگ</b>\n\n"
                "زمانی که کانفیگ را در پنل می‌سازید، اگر اینباند <b>ریمارک (Remark)</b> دارد، "
                "ابتدای نام کانفیگ اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن در نام کانفیگ بیاید، پیشوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>%E2%9A%95%EF%B8%8FTUN_-</code>\n"
                "یا: <code>⚕️TUN_-</code>",
                reply_markup=kb)
            return

        if sn == "admin_bulk_prefix" and is_admin(uid):
            prefix = (message.text or "").strip()
            pkg_id = sd["package_id"]
            state_set(uid, "admin_bulk_suffix",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      has_inquiry=sd["has_inquiry"], count=sd["count"], prefix=prefix)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏭ بعدی (بدون پسوند)", callback_data=f"adm:cfg:bulk:skipsuf:{pkg_id}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:add_config"))
            bot.send_message(uid,
                "✂️ <b>پسوند حذفی از نام کانفیگ</b>\n\n"
                "وقتی چندتا <b>اکسترنال پروکسی</b> ست می‌کنید، انتهای نام کانفیگ متن‌های اضافه اکسترنال‌ها اضافه می‌شود.\n"
                "اگر نمی‌خواهید آن‌ها در نام کانفیگ بیاید، پسوند را اینجا وارد کنید.\n\n"
                "💡 مثال: <code>-main</code>",
                reply_markup=kb)
            return

        if sn == "admin_bulk_suffix" and is_admin(uid):
            suffix = (message.text or "").strip()
            has_inq = sd.get("has_inquiry", False)
            count = sd.get("count", 0)
            prefix = sd.get("prefix", "")
            state_set(uid, "admin_bulk_data",
                      package_id=sd["package_id"], type_id=sd["type_id"],
                      has_inquiry=has_inq, count=count, prefix=prefix, suffix=suffix)
            if has_inq:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    f"تعداد: <b>{count}</b> کانفیگ\n\n"
                    "هر کانفیگ <b>دو خط</b> دارد:\n"
                    "خط اول: لینک کانفیگ\n"
                    "خط دوم: لینک استعلام (شروع با http)\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "http://panel.com/sub/1\n"
                    "vless://def...#name2\n"
                    "http://panel.com/sub/2</code>"
                )
            else:
                fmt_text = (
                    "📋 <b>ارسال کانفیگ‌ها</b>\n\n"
                    f"تعداد: <b>{count}</b> کانفیگ\n\n"
                    "هر خط یک لینک کانفیگ:\n\n"
                    "💡 مثال:\n"
                    "<code>vless://abc...#name1\n"
                    "vless://def...#name2</code>"
                )
            bot.send_message(uid, fmt_text, reply_markup=back_button("admin:add_config"))
            return

        if sn == "admin_bulk_data" and is_admin(uid):
            raw = (message.text or "").strip()
            if not raw:
                bot.send_message(uid, "⚠️ متنی ارسال نشده.", reply_markup=back_button("admin:add_config"))
                return
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            has_inq = sd.get("has_inquiry", False)
            expected = sd.get("count", 0)
            prefix = sd.get("prefix", "")
            suffix = sd.get("suffix", "")
            type_id = sd["type_id"]
            package_id = sd["package_id"]

            configs = []
            if has_inq:
                # Pair lines: config, inquiry, config, inquiry...
                i = 0
                while i < len(lines):
                    cfg_line = lines[i]
                    inq_line = lines[i + 1] if i + 1 < len(lines) and lines[i + 1].lower().startswith("http") else ""
                    configs.append((cfg_line, inq_line))
                    i += 2 if inq_line else 1
            else:
                for line in lines:
                    configs.append((line, ""))

            success_count = 0
            errors = []
            for idx, (cfg_text, inq_link) in enumerate(configs, 1):
                # Extract name from after #
                if "#" in cfg_text:
                    raw_name = cfg_text.rsplit("#", 1)[1]
                else:
                    raw_name = f"config-{idx}"
                # URL-decode the name
                try:
                    svc_name = urllib.parse.unquote(raw_name)
                except Exception:
                    svc_name = raw_name
                # Strip prefix
                if prefix and svc_name.startswith(prefix):
                    svc_name = svc_name[len(prefix):]
                # Also try URL-decoded prefix
                if prefix:
                    try:
                        decoded_prefix = urllib.parse.unquote(prefix)
                        if decoded_prefix != prefix and svc_name.startswith(decoded_prefix):
                            svc_name = svc_name[len(decoded_prefix):]
                    except Exception:
                        pass
                # Strip suffix
                if suffix and svc_name.endswith(suffix):
                    svc_name = svc_name[:-len(suffix)]
                if suffix:
                    try:
                        decoded_suffix = urllib.parse.unquote(suffix)
                        if decoded_suffix != suffix and svc_name.endswith(decoded_suffix):
                            svc_name = svc_name[:-len(decoded_suffix)]
                    except Exception:
                        pass
                svc_name = svc_name.strip().strip("-").strip("_").strip()
                if not svc_name:
                    svc_name = f"config-{idx}"
                if not cfg_text:
                    errors.append(f"کانفیگ {idx}: متن خالی")
                    continue
                try:
                    add_config(type_id, package_id, svc_name, cfg_text, inq_link)
                    success_count += 1
                except Exception as e:
                    errors.append(f"کانفیگ {idx}: {str(e)}")

            state_clear(uid)
            result = f"✅ <b>{success_count}</b> کانفیگ از <b>{expected}</b> با موفقیت ثبت شد."
            if len(configs) != expected:
                result += f"\n\n⚠️ تعداد ارسال‌شده ({len(configs)}) با تعداد مورد انتظار ({expected}) متفاوت است."
            if errors:
                result += "\n\n❌ خطاها:\n" + "\n".join(errors[:20])
            bot.send_message(uid, result, reply_markup=kb_admin_panel())
            return

        # ── Admin: Settings ────────────────────────────────────────────────────
        if sn == "admin_set_support" and is_admin(uid):
            setting_set("support_username", (message.text or "").strip())
            state_clear(uid)
            bot.send_message(uid, "✅ آیدی پشتیبانی ذخیره شد.", reply_markup=back_button("adm:set:support"))
            return

        if sn == "admin_set_support_link" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("support_link", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ لینک پشتیبانی ذخیره شد.", reply_markup=back_button("adm:set:support"))
            return

        if sn == "admin_set_support_desc" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("support_link_desc", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ توضیحات پشتیبانی ذخیره شد.", reply_markup=back_button("adm:set:support"))
            return

        if sn == "admin_set_card" and is_admin(uid):
            setting_set("payment_card", normalize_text_number(message.text or ""))
            state_clear(uid)
            bot.send_message(uid, "✅ شماره کارت ذخیره شد.", reply_markup=back_button("adm:set:gw:card"))
            return

        if sn == "admin_set_bank" and is_admin(uid):
            setting_set("payment_bank", (message.text or "").strip())
            state_clear(uid)
            bot.send_message(uid, "✅ نام بانک ذخیره شد.", reply_markup=back_button("adm:set:gw:card"))
            return

        if sn == "admin_set_owner" and is_admin(uid):
            setting_set("payment_owner", (message.text or "").strip())
            state_clear(uid)
            bot.send_message(uid, "✅ نام صاحب کارت ذخیره شد.", reply_markup=back_button("adm:set:gw:card"))
            return

        if sn == "admin_set_crypto_wallet" and is_admin(uid):
            coin_key = sd["coin_key"]
            val      = (message.text or "").strip()
            setting_set(f"crypto_{coin_key}", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ آدرس ولت ذخیره شد.", reply_markup=back_button("adm:set:gw:crypto"))
            return

        if sn == "admin_set_tetrapay_key" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("tetrapay_api_key", val)
            state_clear(uid)
            bot.send_message(uid, "✅ کلید API تتراپی ذخیره شد.", reply_markup=back_button("adm:set:gw:tetrapay"))
            return

        if sn == "admin_set_swapwallet_key" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("swapwallet_api_key", val)
            state_clear(uid)
            bot.send_message(uid, "✅ کلید API سواپ ولت ذخیره شد.", reply_markup=back_button("adm:set:gw:swapwallet"))
            return

        if sn == "admin_set_swapwallet_username" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("swapwallet_username", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ نام کاربری فروشگاه سواپ ولت ذخیره شد.", reply_markup=back_button("adm:set:gw:swapwallet"))
            return

        if sn == "admin_set_channel" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("channel_id", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ کانال ذخیره شد.", reply_markup=back_button("admin:settings"))
            return

        if sn == "admin_set_start_text" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("start_text", "" if val == "-" else val)
            state_clear(uid)
            bot.send_message(uid, "✅ متن استارت ذخیره شد.", reply_markup=back_button("admin:settings"))
            return

        # ── Admin: Free Test settings ──────────────────────────────────────────
        if sn == "admin_set_agent_test_limit" and is_admin(uid):
            val = (message.text or "").strip()
            if val == "0":
                setting_set("agent_test_limit", "0")
                state_clear(uid)
                bot.send_message(uid, "✅ محدودیت تست همکاران غیرفعال شد.", reply_markup=back_button("adm:set:freetest"))
                return
            parts = val.split()
            if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("day", "week", "month"):
                bot.send_message(uid,
                    "⚠️ فرمت نادرست. مثال: <code>5 day</code> یا <code>10 week</code> یا <code>20 month</code>\nبرای غیرفعال: <code>0</code>",
                    reply_markup=back_button("adm:set:freetest"))
                return
            setting_set("agent_test_limit", parts[0])
            setting_set("agent_test_period", parts[1])
            state_clear(uid)
            period_labels = {"day": "روز", "week": "هفته", "month": "ماه"}
            bot.send_message(uid,
                f"✅ تست همکاران: {parts[0]} عدد در {period_labels[parts[1]]}",
                reply_markup=back_button("adm:set:freetest"))
            return

        # ── Admin: Backup settings ─────────────────────────────────────────────
        if sn == "admin_set_backup_interval" and is_admin(uid):
            val = parse_int(message.text or "")
            if not val or val < 1:
                bot.send_message(uid, "⚠️ عدد معتبر وارد کنید.", reply_markup=back_button("admin:backup"))
                return
            setting_set("backup_interval", str(val))
            state_clear(uid)
            bot.send_message(uid, f"✅ بازه بکاپ به {val} ساعت تنظیم شد.", reply_markup=back_button("admin:backup"))
            return

        if sn == "admin_set_backup_target" and is_admin(uid):
            val = (message.text or "").strip()
            setting_set("backup_target_id", val)
            state_clear(uid)
            bot.send_message(uid, "✅ مقصد بکاپ ذخیره شد.", reply_markup=back_button("admin:backup"))
            return

        if sn == "admin_restore_backup" and is_admin(uid):
            if not message.document:
                bot.send_message(uid, "⚠️ لطفاً فایل بکاپ (.db) را ارسال کنید.", reply_markup=back_button("admin:backup"))
                return
            file_name = message.document.file_name or ""
            if not file_name.lower().endswith(".db"):
                bot.send_message(uid, "⚠️ فقط فایل با پسوند <code>.db</code> قابل قبول است.", reply_markup=back_button("admin:backup"))
                return
            try:
                file_info = bot.get_file(message.document.file_id)
                downloaded = bot.download_file(file_info.file_path)
                # ابتدا بکاپ از دیتابیس فعلی
                import shutil
                backup_ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                pre_restore_backup = f"{DB_NAME}.pre_restore_{backup_ts}"
                if os.path.exists(DB_NAME):
                    shutil.copy2(DB_NAME, pre_restore_backup)
                # جایگزینی دیتابیس
                with open(DB_NAME, "wb") as f:
                    f.write(downloaded)
                state_clear(uid)
                bot.send_message(uid,
                    f"✅ بکاپ با موفقیت بازیابی شد.\n\n"
                    f"💾 نسخه قبلی در <code>{esc(pre_restore_backup)}</code> ذخیره شد.",
                    reply_markup=back_button("admin:backup"))
            except Exception as e:
                bot.send_message(uid, f"❌ خطا در بازیابی بکاپ: {esc(str(e))}", reply_markup=back_button("admin:backup"))
            return

        # ── Admin: Stock Search ────────────────────────────────────────────────
        if sn in ("admin_search_by_link", "admin_search_by_config", "admin_search_by_name") and is_admin(uid):
            query_text = (message.text or "").strip()
            if not query_text:
                bot.send_message(uid, "⚠️ متن جستجو را ارسال کنید.")
                return
            state_clear(uid)
            search_param = f"%{query_text}%"
            if sn == "admin_search_by_link":
                col_filter = "c.inquiry_link LIKE ?"
            elif sn == "admin_search_by_config":
                col_filter = "c.config_text LIKE ?"
            else:
                col_filter = "c.service_name LIKE ?"
            with get_conn() as conn:
                rows = conn.execute(
                    f"SELECT c.id, c.service_name, c.sold_to, c.is_expired FROM configs c WHERE {col_filter} ORDER BY c.id DESC LIMIT 50",
                    (search_param,)
                ).fetchall()
            if not rows:
                bot.send_message(uid, "❌ نتیجه‌ای یافت نشد.", reply_markup=back_button("adm:stk:search"))
                return
            kb = types.InlineKeyboardMarkup()
            for r in rows:
                label = r["service_name"] or f"#{r['id']}"
                if r["is_expired"]:
                    label = "⛔ " + label
                elif r["sold_to"]:
                    label = "✅ " + label
                else:
                    label = "📦 " + label
                kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:stk:cfg:{r['id']}"))
            kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="adm:stk:search"))
            bot.send_message(uid, f"🔍 نتایج جستجو ({len(rows)}):", reply_markup=kb)
            return

        # ── Admin: Balance edit ────────────────────────────────────────────────
        if sn in ("admin_bal_add", "admin_bal_sub") and is_admin(uid):
            amount        = parse_int(message.text or "")
            target_user_id = sd["target_user_id"]
            if not amount or amount <= 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("admin:users"))
                return
            delta = amount if sn == "admin_bal_add" else -amount
            update_balance(target_user_id, delta)
            state_clear(uid)
            action_label = "اضافه" if delta > 0 else "کاهش"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 بازگشت به کاربر", callback_data=f"adm:usr:v:{target_user_id}"))
            bot.send_message(uid, f"✅ موجودی {action_label} یافت.", reply_markup=kb)
            try:
                msg = f"{'➕' if delta > 0 else '➖'} موجودی شما توسط ادمین {action_label} یافت.\n💰 مبلغ: {fmt_price(abs(amount))} تومان"
                bot.send_message(target_user_id, msg)
            except Exception:
                pass
            return

        # ── Admin: Agency price ────────────────────────────────────────────────
        if sn == "admin_set_agency_price" and is_admin(uid):
            target_user_id = sd["target_user_id"]
            package_id     = sd["package_id"]
            val            = parse_int(message.text or "")
            if val is None or val < 0:
                bot.send_message(uid, "⚠️ مبلغ معتبر وارد کنید.", reply_markup=back_button("admin:users"))
                return
            if val == 0:
                with get_conn() as conn:
                    conn.execute("DELETE FROM agency_prices WHERE user_id=? AND package_id=?",
                                 (target_user_id, package_id))
                state_clear(uid)
                bot.send_message(uid, "✅ قیمت اختصاصی حذف شد (قیمت پیش‌فرض اعمال می‌شود).",
                                 reply_markup=kb_admin_panel())
            else:
                set_agency_price(target_user_id, package_id, val)
                state_clear(uid)
                bot.send_message(uid, f"✅ قیمت اختصاصی {fmt_price(val)} تومان ثبت شد.",
                                 reply_markup=kb_admin_panel())
            return

        # ── Admin: Payment approval ────────────────────────────────────────────
        if sn == "admin_payment_approve_note" and is_admin(uid):
            payment_id = sd["payment_id"]
            note       = (message.text or "").strip() or "واریزی شما تأیید شد."
            finish_card_payment_approval(payment_id, note, approved=True)
            state_clear(uid)
            bot.send_message(uid, "✅ درخواست با موفقیت تأیید شد.", reply_markup=kb_admin_panel())
            return

        if sn == "admin_payment_reject_note" and is_admin(uid):
            payment_id = sd["payment_id"]
            note       = (message.text or "").strip() or "رسید شما رد شد."
            finish_card_payment_approval(payment_id, note, approved=False)
            state_clear(uid)
            bot.send_message(uid, "✅ درخواست با موفقیت رد شد.", reply_markup=kb_admin_panel())
            return

        # ── Agency request text ────────────────────────────────────────────────
        if sn == "agency_request_text":
            req_text = (message.text or "").strip() or "بدون متن"
            state_clear(uid)
            user = get_user(uid)
            bot.send_message(uid, "✅ درخواست نمایندگی شما ارسال شد.\n⏳ لطفاً منتظر بررسی ادمین باشید.",
                             reply_markup=kb_main(uid))
            text = (
                f"🤝 <b>درخواست نمایندگی جدید</b>\n\n"
                f"👤 نام: {esc(user['full_name'])}\n"
                f"🆔 نام کاربری: {esc(display_username(user['username']))}\n"
                f"🔢 آیدی: <code>{user['user_id']}</code>\n\n"
                f"📝 متن درخواست:\n{esc(req_text)}"
            )
            admin_kb = types.InlineKeyboardMarkup()
            admin_kb.row(
                types.InlineKeyboardButton("✅ تأیید", callback_data=f"agency:approve:{uid}"),
                types.InlineKeyboardButton("❌ رد", callback_data=f"agency:reject:{uid}"),
            )
            for admin_id in ADMIN_IDS:
                try:
                    bot.send_message(admin_id, text, reply_markup=admin_kb)
                except Exception:
                    pass
            return

        # ── Agency approval note ───────────────────────────────────────────────
        if sn == "agency_approve_note" and is_admin(uid):
            note = (message.text or "").strip()
            target_uid = sd["target_user_id"]
            state_clear(uid)
            with get_conn() as conn:
                conn.execute("UPDATE users SET is_agent=1 WHERE user_id=?", (target_uid,))
            bot.send_message(uid, "✅ نمایندگی تأیید شد.")
            _show_admin_user_detail_msg(uid, target_uid)
            try:
                msg = "🎉 <b>درخواست نمایندگی شما تأیید شد!</b>\n\nاکنون شما نماینده هستید."
                if note:
                    msg += f"\n\n📝 پیام ادمین:\n{esc(note)}"
                bot.send_message(target_uid, msg)
            except Exception:
                pass
            return

        # ── Agency rejection reason ────────────────────────────────────────────
        if sn == "agency_reject_reason" and is_admin(uid):
            reason = (message.text or "").strip() or "بدون دلیل"
            target_uid = sd["target_user_id"]
            state_clear(uid)
            bot.send_message(uid, "✅ درخواست نمایندگی رد شد.", reply_markup=kb_admin_panel())
            try:
                bot.send_message(target_uid,
                    f"❌ <b>درخواست نمایندگی شما رد شد.</b>\n\n📝 دلیل:\n{esc(reason)}")
            except Exception:
                pass
            return

        # ── Admin: Edit rules text ─────────────────────────────────────────────
        if sn == "admin_edit_rules_text" and is_admin(uid):
            text_val = (message.text or "").strip()
            if not text_val:
                bot.send_message(uid, "⚠️ متن خالی مجاز نیست.", reply_markup=back_button("adm:set:rules"))
                return
            setting_set("purchase_rules_text", text_val)
            state_clear(uid)
            bot.send_message(uid, "✅ متن قوانین خرید ذخیره شد.", reply_markup=back_button("adm:set:rules"))
            return

    except Exception as e:
        print("TEXT_HANDLER_ERROR:", e)
        traceback.print_exc()
        state_clear(uid)
        bot.send_message(uid, "⚠️ خطایی رخ داد. لطفاً دوباره از منو ادامه دهید.", reply_markup=kb_main(uid))
        return

    # Fallback
    if message.content_type == "text":
        if message.text == "/start":
            return
        bot.send_message(uid, "لطفاً از دکمه‌های منو استفاده کنید.", reply_markup=kb_main(uid))

# ── Bootstrap ──────────────────────────────────────────────────────────────────
def main():
    init_db()
    set_bot_commands()

    # Start backup thread
    backup_thread = threading.Thread(target=_backup_loop, daemon=True)
    backup_thread.start()

    print("✅ Bot v4 is running...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)

if __name__ == "__main__":
    main()
