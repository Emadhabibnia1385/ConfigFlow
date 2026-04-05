# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv

load_dotenv()

# ── Environment ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_NAME   = os.getenv("DB_NAME", "configflow.db")

# ── Branding & External API URLs ───────────────────────────────────────────────
BRAND_TITLE          = "ConfigFlow"
DEFAULT_ADMIN_HANDLE = ""
CRYPTO_PRICES_API    = "https://swapwallet.app/api/v1/market/prices"
TETRAPAY_CREATE_URL  = "https://tetra98.com/api/create_order"
TETRAPAY_VERIFY_URL  = "https://tetra98.com/api/verify"
SWAPWALLET_BASE_URL  = "https://swapwallet.app/api"

# ── Admin Permission System ────────────────────────────────────────────────────
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
    ("manage_panels",    "🖥 مدیریت پنل‌های 3x-ui"),
]
PERM_FULL_SET = {
    "types_packages", "register_config", "view_configs", "manage_configs",
    "broadcast_all", "broadcast_cust", "view_users", "agency", "assign_config",
    "manage_balance", "user_status", "full_users", "settings",
    "approve_payments", "approve_renewal", "manage_panels",
}
PERM_USER_FULL = {"agency", "assign_config", "manage_balance", "user_status"}

# ── Crypto Configuration ───────────────────────────────────────────────────────
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

# ── Pagination ─────────────────────────────────────────────────────────────────
CONFIGS_PER_PAGE = 10

# ── Validation ─────────────────────────────────────────────────────────────────
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise SystemExit("BOT_TOKEN تنظیم نشده یا معتبر نیست.")
if not ADMIN_IDS:
    raise SystemExit("ADMIN_IDS تنظیم نشده است.")
