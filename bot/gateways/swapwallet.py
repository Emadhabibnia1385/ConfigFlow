# -*- coding: utf-8 -*-
"""
SwapWallet USDT payment gateway — invoice creation, status check, and UI pages.
"""
import json
import urllib.request
import urllib.parse
import urllib.error

from ..config import SWAPWALLET_BASE_URL
from ..db import setting_get
from ..helpers import fmt_price, esc
from ..bot_instance import bot
from telebot import types

SWAPWALLET_NETWORK_LABELS = {
    "TRON": "ترون (TRC-20)",
    "TON":  "تون (TON)",
    "BSC":  "بایننس (BEP-20)",
}


def _swapwallet_crypto_line(amount_toman, api_result):
    """Return (crypto_amount_str, token, network_label) for display."""
    usd_val    = api_result.get("amount", {}).get("usdValue", {})
    usd_amount = (usd_val or {}).get("number", "").strip()
    usd_unit   = (usd_val or {}).get("unit", "USDT").strip() or "USDT"
    cfg_net    = setting_get("swapwallet_network", "TRON")
    network    = SWAPWALLET_NETWORK_LABELS.get(cfg_net, cfg_net)
    if not usd_amount:
        from .crypto import fetch_crypto_prices
        prices       = fetch_crypto_prices()
        irt_per_usdt = prices.get("USDT", 0)
        if irt_per_usdt > 0:
            usd_amount = str(round(amount_toman / irt_per_usdt, 4))
    return usd_amount, usd_unit, network


def create_swapwallet_invoice(amount_toman, order_id, description="پرداخت"):
    api_key  = setting_get("swapwallet_api_key", "").strip()
    username = setting_get("swapwallet_username", "").strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    if username.startswith("@"):
        username = username[1:].strip()
    if not api_key or not username:
        return False, {"error": "کلید API یا نام کاربری فروشگاه سواپ ولت تنظیم نشده است. از پنل مدیریت ← تنظیمات ← درگاه‌ها اقدام کنید."}
    cfg_net = setting_get("swapwallet_network", "TRON")
    payload = json.dumps({
        "amount":       {"number": str(int(amount_toman)), "unit": "IRT"},
        "network":      cfg_net,
        "allowedToken": "USDT",
        "ttl":          3600,
        "orderId":      str(order_id),
        "description":  str(description),
    }, ensure_ascii=False).encode("utf-8")
    safe_user = urllib.parse.quote(username, safe="")
    url = f"{SWAPWALLET_BASE_URL}/v2/payment/{safe_user}/invoices/temporary-wallet"
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type":  "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
            "User-Agent":    "ConfigFlow/1.0",
            "Accept":        "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("status") == "OK":
            return True, result.get("result", {})
        return False, {"error": str(result)}
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            msg = err_data.get("message") or err_data.get("error") or str(err_data)[:200]
        except Exception:
            msg = f"HTTP {e.code}: {e.reason}"
        return False, {"error": msg}
    except Exception as e:
        return False, {"error": str(e)}


def check_swapwallet_invoice(invoice_id):
    api_key  = setting_get("swapwallet_api_key", "").strip()
    username = setting_get("swapwallet_username", "").strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    if username.startswith("@"):
        username = username[1:].strip()
    if not api_key or not username:
        return False, {"error": "کلید API یا نام کاربری فروشگاه سواپ ولت تنظیم نشده است."}
    safe_user = urllib.parse.quote(username, safe="")
    safe_inv  = urllib.parse.quote(str(invoice_id), safe="")
    url = f"{SWAPWALLET_BASE_URL}/v2/payment/{safe_user}/invoices/{safe_inv}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent":    "ConfigFlow/1.0",
            "Accept":        "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("status") == "OK":
            return True, result.get("result", {})
        return False, {"error": str(result)}
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            msg = err_data.get("message") or err_data.get("error") or str(err_data)[:200]
        except Exception:
            msg = f"HTTP {e.code}: {e.reason}"
        return False, {"error": msg}
    except Exception as e:
        return False, {"error": str(e)}


def show_swapwallet_page(call, *, amount_toman, invoice_id, wallet_address,
                          usd_amount, usd_unit, network_label, links,
                          payment_id, verify_cb):
    """Render the SwapWallet payment page."""
    from ..ui.helpers import send_or_edit
    short_id    = invoice_id.replace("-", "")[:10] if invoice_id else "---"
    crypto_line = f"<b>{esc(usd_amount)}</b>" if usd_amount else "—"
    text = (
        "✅ <b>فاکتور پرداخت ایجاد شد</b>\n\n"
        f"🛒 کد پیگیری: <code>{short_id}</code>\n"
        f"💲 مبلغ (تومان): <b>{fmt_price(amount_toman)}</b>\n\n"
        f"📡 شبکه: {esc(network_label)} — {esc(usd_unit)}\n"
        f"📬 آدرس کیف پول:\n<code>{esc(wallet_address)}</code>\n\n"
        f"💰 مقدار دقیق ({esc(usd_unit)}): {crypto_line}\n\n"
        "💢 <b>قبل از پرداخت بخوانید:</b>\n"
        "❌ این فاکتور <b>۱ ساعت</b> اعتبار دارد\n"
        "🔸 در صورت اشتباه در آدرس، تراکنش تأیید نمی‌شود و برگشت وجه ممکن نیست\n"
        "🔹 مبلغ ارسالی نباید کمتر یا بیشتر از مقدار اعلام‌شده باشد\n"
        "🔹 در صورت واریز بیشتر از مقدار گفته‌شده، امکان برگشت وجه وجود ندارد"
    )
    kb = types.InlineKeyboardMarkup()
    for link in links:
        ln, lu = link.get("name", ""), link.get("url", "")
        if lu:
            label = "💎 پرداخت با سواپ ولت" if ln == "SWAP_WALLET" else f"👛 {ln}"
            kb.add(types.InlineKeyboardButton(label, url=lu))
    kb.add(types.InlineKeyboardButton("✅ بررسی پرداخت", callback_data=verify_cb))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    bot.answer_callback_query(call.id)
    send_or_edit(call, text, kb)


def swapwallet_error_page(call, err_msg):
    """Show a descriptive error message for SwapWallet failures."""
    from ..ui.helpers import send_or_edit
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="nav:main"))
    bot.answer_callback_query(call.id)
    send_or_edit(call,
        "❌ <b>خطا در ایجاد فاکتور سواپ ولت</b>\n\n"
        f"<code>{esc(str(err_msg)[:400])}</code>\n\n"
        "⚠️ لطفاً موارد زیر را بررسی کنید:\n"
        "• کلید API با فرمت <code>apikey-xxx</code> معتبر باشد\n"
        "• نام کاربری فروشگاه <b>بدون @</b> وارد شده باشد\n"
        "• فروشگاه در پنل سواپ ولت ایجاد شده باشد",
        kb)
