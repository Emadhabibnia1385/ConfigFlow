# -*- coding: utf-8 -*-
"""
TronPays Rial payment gateway — create and check invoices via REST API.
API docs: https://api.tronpays.online/docs#/
"""

import json
import hashlib
import urllib.request
import urllib.error

from ..db import setting_get


TRONPAYS_RIAL_BASE_URL = "https://api.tronpays.online"


def _make_hash_id(raw: str) -> str:
    """Return a <=20-char hash derived from raw — satisfies TronPays max-20 constraint."""
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:20]


def _decode_response_body(resp) -> tuple[str, object]:
    """
    Read response and try to parse JSON.
    Returns: (raw_text, parsed_data_or_raw_text)
    """
    raw = resp.read().decode("utf-8", errors="replace").strip()
    if not raw:
        return "", ""

    try:
        return raw, json.loads(raw)
    except Exception:
        return raw, raw


def _extract_error_message(data) -> str:
    """Extract a readable error message from API error payload."""
    if isinstance(data, dict):
        if "detail" in data:
            try:
                return json.dumps(data["detail"], ensure_ascii=False)[:500]
            except Exception:
                return str(data["detail"])[:500]
        if "message" in data:
            return str(data["message"])[:500]
        return json.dumps(data, ensure_ascii=False)[:500]

    if isinstance(data, list):
        try:
            return json.dumps(data, ensure_ascii=False)[:500]
        except Exception:
            return str(data)[:500]

    return str(data)[:500]


def _post_tronpays(path: str, payload: dict, timeout: int = 15):
    """
    Send POST request to TronPays.
    Returns: (success: bool, parsed_response: object)
    """
    url = f"{TRONPAYS_RIAL_BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "ConfigFlow/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _, parsed = _decode_response_body(resp)
        return True, parsed

    except urllib.error.HTTPError as e:
        try:
            raw_body = e.read().decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(raw_body) if raw_body else {}
            except Exception:
                parsed = raw_body or f"HTTP {e.code}: {e.reason}"
        except Exception:
            parsed = f"HTTP {e.code}: {e.reason}"

        return False, {"error": _extract_error_message(parsed), "status_code": e.code, "raw": parsed}

    except Exception as e:
        return False, {"error": str(e)}


def create_tronpays_rial_invoice(amount_toman, hash_id, description=""):
    """
    Create a TronPays invoice.

    Returns:
        (True, {"invoice_id": ..., "invoice_url": ...}) on success
        (False, {"error": ...}) on failure
    """
    api_key = setting_get("tronpays_rial_api_key", "").strip()
    if not api_key:
        return False, {
            "error": "کلید API ترون‌پیز ثبت نشده است. از پنل مدیریت ← تنظیمات ← درگاه‌ها اقدام کنید."
        }

    safe_hash_id = _make_hash_id(str(hash_id))

    # API schema only accepts: api_key, hash_id, amount
    payload = {
        "api_key": api_key,
        "hash_id": safe_hash_id,
        "amount": int(amount_toman),
    }

    success, result = _post_tronpays("/api/invoice/create", payload)

    if not success:
        return False, result

    print("[TronPays] create invoice raw response:", result)

    # Normalize response — try multiple possible key names
    if isinstance(result, dict):
        invoice_id = (
            result.get("invoice_id")
            or result.get("id")
            or result.get("invoiceId")
            or result.get("invoice")
            or result.get("order_id")
        )
        invoice_url = (
            result.get("invoice_url")
            or result.get("payment_url")
            or result.get("url")
            or result.get("link")
            or result.get("payment_link")
            or result.get("pay_url")
            or result.get("pay_link")
        )

        if invoice_id and invoice_url:
            return True, {"invoice_id": str(invoice_id), "invoice_url": str(invoice_url)}

        # Keys not found — return raw response as error so it can be debugged
        return False, {
            "error": f"پاسخ API ناشناخته است. لطفاً API ترون‌پیز را بررسی کنید.\nجواب API: {json.dumps(result, ensure_ascii=False)[:500]}"
        }

    # String response — might be the payment URL directly
    if isinstance(result, str) and result.startswith("http"):
        return False, {
            "error": f"API یک URL برگرداند اما invoice_id مشخص نیست: {result[:200]}"
        }

    return False, {
        "error": f"پاسخ API ناشناخته: {str(result)[:300]}"
    }


def check_tronpays_rial_invoice(invoice_id):
    """
    Check the status of a TronPays invoice.

    Returns:
        (True, response_data) on success
        (False, {"error": ...}) on failure
    """
    api_key = setting_get("tronpays_rial_api_key", "").strip()
    if not api_key:
        return False, {"error": "کلید API ثبت نشده است."}

    payload = {
        "api_key": api_key,
        "invoice_id": str(invoice_id),
    }

    success, result = _post_tronpays("/api/invoice/check", payload)

    if not success:
        print(f"[TronPays] check invoice FAILED invoice_id={invoice_id!r} result={result!r}")
        return False, result

    print(f"[TronPays] check invoice OK invoice_id={invoice_id!r} result={result!r}")
    return True, result


_PAID_VALUES = {"paid", "success", "successful", "completed", "done", "confirmed", "approved"}


def is_tronpays_paid(status) -> bool:
    """
    Best-effort detection of successful payment from TronPays check response.

    Handles: dict (flat or nested under 'data'), string, int (1 = paid).
    """
    if isinstance(status, dict):
        # Unwrap common nested wrapper: {"data": {...}}
        inner = status.get("data")
        if isinstance(inner, dict):
            if is_tronpays_paid(inner):
                return True

        if status.get("paid") is True or status.get("paid") == 1:
            return True

        for key in ("status", "state", "payment_status", "invoice_status",
                    "transaction_status", "result"):
            value = status.get(key)
            if isinstance(value, str) and value.strip().lower() in _PAID_VALUES:
                return True
            if value == 1 and key in ("paid",):
                return True

        return False

    if isinstance(status, str):
        normalized = status.strip().lower()
        # Handle JSON-encoded string: '"paid"'
        if normalized.startswith('"') and normalized.endswith('"'):
            normalized = normalized[1:-1]
        return normalized in _PAID_VALUES

    return False