# -*- coding: utf-8 -*-
"""
Crypto price fetching from SwapWallet market API.
"""
import json
import urllib.request

from ..config import CRYPTO_PRICES_API


def fetch_crypto_prices():
    """Return dict of {symbol: float(price_in_IRT)} or {} on error."""
    try:
        req = urllib.request.Request(
            CRYPTO_PRICES_API,
            headers={"User-Agent": "ConfigFlow/1.0"}
        )
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
