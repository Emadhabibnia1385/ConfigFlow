# -*- coding: utf-8 -*-
"""
TronPays Rial payment gateway — create and check invoices via REST API.
API docs: https://api.tronpays.online/docs
"""
import json
import hashlib
import urllib.request
import urllib.error

from ..db import setting_get


def _make_hash_id(raw: str) -> str:
    """Return a ≤20-char hash derived from raw — satisfies TronPays max-20 constraint."""
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:20]

TRONPAYS_RIAL_BASE_URL = "https://api.tronpays.online"


def create_tronpays_rial_invoice(amount_toman, hash_id, description=""):
    """Create a TronPays invoice. Returns (success, pay_url_or_error)."""
    api_key = setting_get("tronpays_rial_api_key", "").strip()
    if not api_key:
        return False, {"error": "کلید API تران‌پیز ثبت نشده است. از پنل مدیریت ← تنظیمات ← درگاه‌ها اقدام کنید."}
    callback_url = setting_get("tronpays_rial_callback_url", "").strip() or "https://example.com/"
    safe_hash_id = _make_hash_id(str(hash_id))
    payload = json.dumps({
        "api_key":      api_key,
        "hash_id":      safe_hash_id,
        "amount":       int(amount_toman),
        "callback_url": callback_url,
    }).encode("utf-8")
    url = f"{TRONPAYS_RIAL_BASE_URL}/invoice/create"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "ConfigFlow/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw)
        if isinstance(result, dict) and result.get("status") == 200:
            return True, {"invoice_id": result["invoice_id"], "invoice_url": result["invoice_url"]}
        msg = result.get("message", str(result))[:300] if isinstance(result, dict) else str(result)[:300]
        return False, {"error": msg}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            print(f"[TronPays] HTTP {e.code} body: {body}")
            err_data = json.loads(body)
            msg = str(err_data)[:300]
        except Exception:
            msg = f"HTTP {e.code}: {e.reason}"
        return False, {"error": msg}
    except Exception as e:
        return False, {"error": str(e)}


def check_tronpays_rial_invoice(invoice_id):
    """Check the status of a TronPays invoice. Returns (success, status_string)."""
    api_key = setting_get("tronpays_rial_api_key", "").strip()
    if not api_key:
        return False, {"error": "کلید API ثبت نشده است."}
    payload = json.dumps({
        "api_key":    api_key,
        "invoice_id": invoice_id,
    }).encode("utf-8")
    url = f"{TRONPAYS_RIAL_BASE_URL}/invoice/check"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "ConfigFlow/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw)
        return True, result
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            msg = str(err_data)[:300]
        except Exception:
            msg = f"HTTP {e.code}: {e.reason}"
        return False, {"error": msg}
    except Exception as e:
        return False, {"error": str(e)}


def is_tronpays_paid(status) -> bool:
    """Return True if the TronPays check response indicates a successful payment."""
    if isinstance(status, dict):
        return status.get("status") == 200 and status.get("paid") is True
    return False
