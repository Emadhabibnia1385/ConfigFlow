# -*- coding: utf-8 -*-
"""
TetraPay payment gateway — create and verify orders.
"""
import json
import urllib.request

from ..config import TETRAPAY_CREATE_URL, TETRAPAY_VERIFY_URL
from ..db import setting_get


def create_tetrapay_order(amount_toman, hash_id, description="پرداخت"):
    api_key = setting_get("tetrapay_api_key", "")
    if not api_key:
        return False, {"error": "API key not set"}
    amount_rial = amount_toman * 10
    payload = json.dumps({
        "ApiKey":      api_key,
        "Hash_id":     hash_id,
        "Amount":      amount_rial,
        "Description": description,
        "Email":       "",
        "Mobile":      "",
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
        "ApiKey":    api_key
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
