# -*- coding: utf-8 -*-
"""
Gateway availability checks shared across all payment gateways.
"""
from ..db import setting_get, get_user


def is_gateway_available(gw_name, user_id):
    """Return True if the named gateway is enabled and visible to this user."""
    enabled = setting_get(f"gw_{gw_name}_enabled", "0")
    if enabled != "1":
        return False
    visibility = setting_get(f"gw_{gw_name}_visibility", "public")
    if visibility == "secure":
        user = get_user(user_id)
        return user and user["status"] == "safe"
    return True


def is_card_info_complete():
    """Return True if all card-to-card payment details have been configured."""
    return all([
        setting_get("payment_card", ""),
        setting_get("payment_bank", ""),
        setting_get("payment_owner", ""),
    ])
