# -*- coding: utf-8 -*-
"""
/start message handler.
"""
from ..db import ensure_user, notify_first_start_if_needed
from ..helpers import state_clear
from ..ui.helpers import check_channel_membership, channel_lock_message
from ..ui.menus import show_main_menu
from ..bot_instance import bot


@bot.message_handler(commands=["start"])
def start_handler(message):
    ensure_user(message.from_user)
    notify_first_start_if_needed(message.from_user)
    state_clear(message.from_user.id)
    if not check_channel_membership(message.from_user.id):
        channel_lock_message(message)
        return
    show_main_menu(message)
