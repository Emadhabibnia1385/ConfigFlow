# -*- coding: utf-8 -*-
import telebot
from .config import BOT_TOKEN

bot        = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
USER_STATE = {}
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
