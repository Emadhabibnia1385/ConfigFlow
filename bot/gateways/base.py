# -*- coding: utf-8 -*-
"""
Gateway availability checks shared across all payment gateways.
"""
from ..db import setting_get, get_user

_ALL_GATEWAYS = ("card", "crypto", "tetrapay", "swapwallet_crypto", "tronpays_rial")


def is_gateway_available(gw_name, user_id, amount=None):
    """Return True if the named gateway is enabled and visible to this user."""
    enabled = setting_get(f"gw_{gw_name}_enabled", "0")
    if enabled != "1":
        return False
    visibility = setting_get(f"gw_{gw_name}_visibility", "public")
    if visibility == "secure":
        user = get_user(user_id)
        if not (user and user["status"] == "safe"):
            return False
    if amount is not None:
        range_enabled = setting_get(f"gw_{gw_name}_range_enabled", "0")
        if range_enabled == "1":
            range_min = setting_get(f"gw_{gw_name}_range_min", "")
            range_max = setting_get(f"gw_{gw_name}_range_max", "")
            if range_min and int(range_min) > amount:
                return False
            if range_max and int(range_max) < amount:
                return False
    return True


def get_global_amount_range(user_id):
    """Return (global_min, global_max) across all enabled+visible gateways that have a range.
    Returns (None, None) if no gateway has any range constraint."""
    global_min = None
    global_max = None
    for gw in _ALL_GATEWAYS:
        if setting_get(f"gw_{gw}_enabled", "0") != "1":
            continue
        vis = setting_get(f"gw_{gw}_visibility", "public")
        if vis == "secure":
            user = get_user(user_id)
            if not (user and user["status"] == "safe"):
                continue
        if gw == "card" and not is_card_info_complete():
            continue
        range_on = setting_get(f"gw_{gw}_range_enabled", "0") == "1"
        if range_on:
            r_min = setting_get(f"gw_{gw}_range_min", "")
            r_max = setting_get(f"gw_{gw}_range_max", "")
            gw_min = int(r_min) if r_min else None
            gw_max = int(r_max) if r_max else None
        else:
            gw_min = None
            gw_max = None
        # global_min = lowest min (or None if any gateway has no min)
        if gw_min is None:
            global_min = None  # at least one gateway accepts any low amount
        elif global_min is not None:
            global_min = min(global_min, gw_min)
        else:
            global_min = gw_min
        # global_max = highest max (or None if any gateway has no max)
        if gw_max is None:
            global_max = None  # at least one gateway accepts any high amount
        elif global_max is not None:
            global_max = max(global_max, gw_max)
        else:
            global_max = gw_max
    return (global_min, global_max)


def get_gateway_range_text(gw_name):
    """Return a short range description for a gateway, e.g. '۵۰۰,۰۰۰ تا ۱,۸۰۰,۰۰۰'.
    Returns '' if range is not enabled."""
    if setting_get(f"gw_{gw_name}_range_enabled", "0") != "1":
        return "بدون محدودیت مبلغی"
    r_min = setting_get(f"gw_{gw_name}_range_min", "")
    r_max = setting_get(f"gw_{gw_name}_range_max", "")
    if r_min and r_max:
        return f"{int(r_min):,} تا {int(r_max):,} تومان"
    elif r_min:
        return f"حداقل {int(r_min):,} تومان — حداکثر ندارد"
    elif r_max:
        return f"حداقل ندارد — حداکثر {int(r_max):,} تومان"
    else:
        return "بدون محدودیت مبلغی"


def is_card_info_complete():
    """Return True if all card-to-card payment details have been configured."""
    return all([
        setting_get("payment_card", ""),
        setting_get("payment_bank", ""),
        setting_get("payment_owner", ""),
    ])


def is_gateway_in_range(gw_name, amount):
    """Return True if amount is within the gateway's allowed range (or range is disabled)."""
    if setting_get(f"gw_{gw_name}_range_enabled", "0") != "1":
        return True
    r_min = setting_get(f"gw_{gw_name}_range_min", "")
    r_max = setting_get(f"gw_{gw_name}_range_max", "")
    if r_min and int(r_min) > amount:
        return False
    if r_max and int(r_max) < amount:
        return False
    return True


def build_gateway_range_guide(gw_label_pairs):
    """Build a guide text listing each gateway's range.
    gw_label_pairs: list of (gw_name, display_label) tuples.
    Returns a string like:
      📋 راهنمای انتخاب درگاه پرداخت:
      • کارت به کارت: ۵۰۰٬۰۰۰ تا ۱٬۸۰۰٬۰۰۰ تومان
      • ارز دیجیتال: بدون محدودیت مبلغی
    """
    lines = []
    for gw_name, label in gw_label_pairs:
        rng = get_gateway_range_text(gw_name)
        lines.append(f"  • {label}: {rng}")
    if not lines:
        return ""
    return "📋 <b>راهنمای انتخاب درگاه پرداخت:</b>\n" + "\n".join(lines)
