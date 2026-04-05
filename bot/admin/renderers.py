# -*- coding: utf-8 -*-
"""
Admin panel renderer helpers — reusable screen-building functions
for types, stock, users, admins, panels.
"""
from telebot import types

from ..config import ADMIN_PERMS
from ..db import (
    get_all_types, get_packages, get_registered_packages_stock,
    get_all_admin_users, get_user, get_user_detail,
    get_panel, get_all_panels, get_panel_packages,
)
from ..helpers import esc, fmt_price, display_username, back_button
from ..bot_instance import bot
from ..ui.helpers import send_or_edit


# ── Types & packages ───────────────────────────────────────────────────────────
def _show_admin_types(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ افزودن نوع جدید", callback_data="admin:type:add"))
    all_types = get_all_types()
    for item in all_types:
        kb.add(types.InlineKeyboardButton(f"🧩 {item['name']}", callback_data="noop"))
        kb.row(
            types.InlineKeyboardButton("✏️ ویرایش", callback_data=f"admin:type:edit:{item['id']}"),
            types.InlineKeyboardButton("🗑 حذف",    callback_data=f"admin:type:del:{item['id']}"),
        )
        kb.add(types.InlineKeyboardButton(
            f"➕ افزودن پکیج برای {item['name']}",
            callback_data=f"admin:pkg:add:t:{item['id']}"
        ))
        packs = get_packages(type_id=item['id'], include_inactive=False)
        for p in packs:
            kb.row(
                types.InlineKeyboardButton(
                    f"📦 {p['name']} | {p['volume_gb']}GB | {fmt_price(p['price'])}ت",
                    callback_data="noop"
                ),
                types.InlineKeyboardButton("✏️", callback_data=f"admin:pkg:edit:{p['id']}"),
                types.InlineKeyboardButton("🗑",  callback_data=f"admin:pkg:del:{p['id']}"),
            )
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(call, "🧩 <b>مدیریت نوع و پکیج‌ها</b>", kb)


# ── Stock ──────────────────────────────────────────────────────────────────────
def _show_admin_stock(call):
    rows = get_registered_packages_stock()
    kb   = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📝 ثبت کانفیگ",   callback_data="admin:add_config"))
    kb.add(types.InlineKeyboardButton("🔍 جستجو",          callback_data="adm:stk:search"))
    total_avail   = sum(r["stock"] for r in rows)
    total_sold    = sum(r["sold_count"] for r in rows)
    total_expired = sum(r["expired_count"] for r in rows)
    kb.row(
        types.InlineKeyboardButton(f"🟢 کل موجود ({total_avail})",  callback_data="adm:stk:all:av:0"),
        types.InlineKeyboardButton(f"🔴 کل فروخته ({total_sold})", callback_data="adm:stk:all:sl:0"),
        types.InlineKeyboardButton(f"❌ کل منقضی ({total_expired})", callback_data="adm:stk:all:ex:0"),
    )
    for row in rows:
        pending_c     = row['pending_count'] if row['pending_count'] else 0
        pending_label = f" ⏳{pending_c}" if pending_c > 0 else ""
        kb.add(types.InlineKeyboardButton(
            f"📦 {row['type_name']} - {row['name']} | 🟢{row['stock']} 🔴{row['sold_count']} ❌{row['expired_count']}{pending_label}",
            callback_data=f"adm:stk:pk:{row['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(call, "📁 <b>کانفیگ‌ها</b>", kb)


# ── Admins management ──────────────────────────────────────────────────────────
def _show_admin_admins_panel(call):
    admins = get_all_admin_users()
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ افزودن ادمین جدید", callback_data="adm:mgr:add"))
    for row in admins:
        user_row = get_user(row["user_id"])
        name = user_row["full_name"] if user_row else f"کاربر {row['user_id']}"
        kb.add(types.InlineKeyboardButton(
            f"👮 {name} | {row['user_id']}",
            callback_data=f"adm:mgr:v:{row['user_id']}"
        ))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    count = len(admins)
    text = (
        f"👮 <b>مدیریت ادمین‌ها</b>\n\n"
        f"تعداد ادمین‌های ثبت‌شده: <b>{count}</b>\n\n"
        "برای مشاهده یا ویرایش دسترسی هر ادمین روی نام آن کلیک کنید."
    )
    send_or_edit(call, text, kb)


def _show_perm_selection(call, uid, target_id, perms, edit_mode=False):
    user_row = get_user(target_id)
    name = user_row["full_name"] if user_row else f"کاربر {target_id}"
    text = (
        f"🔑 <b>انتخاب سطح دسترسی</b>\n\n"
        f"👤 کاربر: {esc(name)} (<code>{target_id}</code>)\n\n"
        "سطح دسترسی‌های مورد نظر را انتخاب کنید:\n"
        "(هر گزینه را بزنید تا فعال/غیرفعال شود)"
    )
    kb = types.InlineKeyboardMarkup()
    for perm_key, perm_label in ADMIN_PERMS:
        checked = bool(perms.get(perm_key))
        icon    = "✅" if checked else "⬜️"
        kb.add(types.InlineKeyboardButton(
            f"{icon} {perm_label}",
            callback_data=f"adm:mgr:pt:{perm_key}"
        ))
    action_label = "💾 ذخیره تغییرات" if edit_mode else "➕ افزودن ادمین"
    kb.add(types.InlineKeyboardButton(action_label, callback_data="adm:mgr:confirm"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:admins"))
    send_or_edit(call, text, kb)


# ── Users list & detail ────────────────────────────────────────────────────────
def _show_admin_users_list(call, page=0):
    from ..db import get_users, count_all_users
    rows  = get_users()
    total = count_all_users()
    rows  = sorted(rows, key=lambda r: (r["full_name"] or "").lower())
    per_page    = 10
    total_pages = max(1, (len(rows) + per_page - 1) // per_page)
    page        = max(0, min(page, total_pages - 1))
    page_rows   = rows[page * per_page:(page + 1) * per_page]
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="adm:usr:search"))
    for row in page_rows:
        status_icon = "🔘" if row["status"] == "safe" else "⚠️"
        agent_icon  = "🤝" if row["is_agent"] else ""
        label = f"{status_icon}{agent_icon} {row['full_name']} | {display_username(row['username'])}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"adm:usr:v:{row['user_id']}"))
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin:users:pg:{page - 1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("➡️ بعدی", callback_data=f"admin:users:pg:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(
        call,
        f"👥 <b>مدیریت کاربران</b>\n\n"
        f"👤 تعداد کل کاربران: <b>{total}</b> نفر\n"
        f"📄 صفحه {page + 1} از {total_pages}",
        kb
    )


def _show_admin_user_detail(call, user_id):
    row = get_user_detail(user_id)
    if not row:
        send_or_edit(call, "کاربر یافت نشد.", back_button("admin:users"))
        return
    status_label = "🔘 امن" if row["status"] == "safe" else "⚠️ ناامن"
    agent_label  = "🤝 نمایندگی فعال" if row["is_agent"] else "❌ نمایندگی غیرفعال"
    text = (
        "👤 <b>اطلاعات کاربر</b>\n\n"
        f"📱 نام: {esc(row['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(row['username']))}\n"
        f"🔢 آیدی: <code>{row['user_id']}</code>\n"
        f"💰 موجودی: <b>{fmt_price(row['balance'])}</b> تومان\n"
        f"🛍 تعداد خرید: <b>{row['purchase_count']}</b>\n"
        f"💵 مجموع خرید: <b>{fmt_price(row['total_spent'])}</b> تومان\n"
        f"🕒 عضویت: {esc(row['joined_at'])}\n"
        f"وضعیت: {status_label}\n"
        f"نمایندگی: {agent_label}"
    )
    uid_t = row["user_id"]
    kb    = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(f"🔄 {status_label}",    callback_data=f"adm:usr:sts:{uid_t}"),
        types.InlineKeyboardButton(f"🤝 نمایندگی",          callback_data=f"adm:usr:ag:{uid_t}"),
    )
    kb.add(types.InlineKeyboardButton("💰 موجودی",           callback_data=f"adm:usr:bal:{uid_t}"))
    kb.add(types.InlineKeyboardButton("📦 کانفیگ‌ها",         callback_data=f"adm:usr:cfgs:{uid_t}"))
    if row["is_agent"]:
        kb.add(types.InlineKeyboardButton("🏷 قیمت‌های نمایندگی", callback_data=f"adm:usr:agp:{uid_t}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:users"))
    send_or_edit(call, text, kb)


def _show_admin_user_detail_msg(chat_id, user_id):
    """Send user detail as a new message (for use from message handlers)."""
    row = get_user_detail(user_id)
    if not row:
        bot.send_message(chat_id, "کاربر یافت نشد.", reply_markup=back_button("admin:users"))
        return
    status_label = "🔘 امن" if row["status"] == "safe" else "⚠️ ناامن"
    agent_label  = "🤝 نمایندگی فعال" if row["is_agent"] else "❌ نمایندگی غیرفعال"
    text = (
        "👤 <b>اطلاعات کاربر</b>\n\n"
        f"📱 نام: {esc(row['full_name'])}\n"
        f"🆔 نام کاربری: {esc(display_username(row['username']))}\n"
        f"🔢 آیدی: <code>{row['user_id']}</code>\n"
        f"💰 موجودی: <b>{fmt_price(row['balance'])}</b> تومان\n"
        f"🛍 تعداد خرید: <b>{row['purchase_count']}</b>\n"
        f"💵 مجموع خرید: <b>{fmt_price(row['total_spent'])}</b> تومان\n"
        f"🕒 عضویت: {esc(row['joined_at'])}\n"
        f"وضعیت: {status_label}\n"
        f"نمایندگی: {agent_label}"
    )
    uid_t = row["user_id"]
    kb    = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(f"🔄 {status_label}",    callback_data=f"adm:usr:sts:{uid_t}"),
        types.InlineKeyboardButton(f"🤝 نمایندگی",          callback_data=f"adm:usr:ag:{uid_t}"),
    )
    kb.add(types.InlineKeyboardButton("💰 موجودی",           callback_data=f"adm:usr:bal:{uid_t}"))
    kb.add(types.InlineKeyboardButton("📦 کانفیگ‌ها",         callback_data=f"adm:usr:cfgs:{uid_t}"))
    if row["is_agent"]:
        kb.add(types.InlineKeyboardButton("🏷 قیمت‌های نمایندگی", callback_data=f"adm:usr:agp:{uid_t}"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:users"))
    bot.send_message(chat_id, text, reply_markup=kb)


def _show_admin_assign_config_type(call, target_id):
    items = get_all_types()
    kb    = types.InlineKeyboardMarkup()
    for item in items:
        kb.add(types.InlineKeyboardButton(
            f"🧩 {item['name']}",
            callback_data=f"adm:acfg:t:{target_id}:{item['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:usr:v:{target_id}"))
    send_or_edit(call, "📝 نوع کانفیگ را انتخاب کنید:", kb)


def _fake_call(call, new_data):
    """Re-dispatch a callback with different data (for re-rendering pages)."""
    from ..handlers.callbacks import _dispatch_callback

    class _FakeCall:
        def __init__(self, original, data):
            self.from_user = original.from_user
            self.message   = original.message
            self.data      = data
            self.id        = original.id

    _dispatch_callback(_FakeCall(call, new_data), call.from_user.id, new_data)


# ── 3x-ui Panel renderers ──────────────────────────────────────────────────────
def _show_admin_panels(call):
    panels = get_all_panels()
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Register Panel Config", callback_data="adm:panel:add"))
    for p in panels:
        status_icon = "🟢" if p["is_active"] else "🔴"
        kb.add(types.InlineKeyboardButton(
            f"{status_icon} {p['name']} | {p['ip']}:{p['port']}",
            callback_data="noop"
        ))
        kb.row(
            types.InlineKeyboardButton("✏️ Edit",     callback_data=f"adm:panel:edit:{p['id']}"),
            types.InlineKeyboardButton("🗑 Delete",   callback_data=f"adm:panel:del:{p['id']}"),
            types.InlineKeyboardButton("📦 Packages", callback_data=f"adm:panel:pkgs:{p['id']}"),
        )
    kb.add(types.InlineKeyboardButton("⚙️ Worker API Settings", callback_data="adm:panel:api_settings"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel"))
    send_or_edit(call, "🖥 <b>مدیریت پنل‌های 3x-ui</b>\n\nپنل‌های ثبت‌شده:", kb)


def _show_panel_packages(call, panel_id):
    panel = get_panel(panel_id)
    if not panel:
        bot.answer_callback_query(call.id, "Panel not found.", show_alert=True)
        return
    pkgs = get_panel_packages(panel_id)
    kb   = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Add Traffic Package", callback_data=f"adm:panel:pkadd:{panel_id}"))
    for pp in pkgs:
        inbound_display = pp['inbound_id'] if 'inbound_id' in pp.keys() else 1
        kb.add(types.InlineKeyboardButton(
            f"📦 {pp['name']} | {pp['volume_gb']}GB | {pp['duration_days']}d | inbound#{inbound_display}",
            callback_data="noop"
        ))
        kb.add(types.InlineKeyboardButton(
            f"🗑 Delete {pp['name']}", callback_data=f"adm:panel:pkdel:{pp['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🔙 Back to Panels", callback_data="admin:panels"))
    send_or_edit(call,
        f"📦 <b>Traffic Packages — {esc(panel['name'])}</b>\n"
        f"🌐 {esc(panel['ip'])}:{panel['port']}\n\n"
        "Packages assigned to this panel:", kb)


def _show_panel_edit(call, panel_id):
    panel = get_panel(panel_id)
    if not panel:
        bot.answer_callback_query(call.id, "Panel not found.", show_alert=True)
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📝 Edit Name",     callback_data=f"adm:panel:ef:name:{panel_id}"))
    kb.add(types.InlineKeyboardButton("🌐 Edit IP",       callback_data=f"adm:panel:ef:ip:{panel_id}"))
    kb.add(types.InlineKeyboardButton("🔌 Edit Port",     callback_data=f"adm:panel:ef:port:{panel_id}"))
    kb.add(types.InlineKeyboardButton("📄 Edit Patch",    callback_data=f"adm:panel:ef:patch:{panel_id}"))
    kb.add(types.InlineKeyboardButton("👤 Edit Username", callback_data=f"adm:panel:ef:username:{panel_id}"))
    kb.add(types.InlineKeyboardButton("🔑 Edit Password", callback_data=f"adm:panel:ef:password:{panel_id}"))
    status_lbl = "🔴 Deactivate" if panel["is_active"] else "🟢 Activate"
    new_status  = 0 if panel["is_active"] else 1
    kb.add(types.InlineKeyboardButton(status_lbl, callback_data=f"adm:panel:toggle:{panel_id}:{new_status}"))
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin:panels"))
    send_or_edit(call,
        f"✏️ <b>Edit Panel — {esc(panel['name'])}</b>\n\n"
        f"🌐 IP: <code>{esc(panel['ip'])}</code>\n"
        f"🔌 Port: <code>{panel['port']}</code>\n"
        f"📄 Patch: <code>{esc(panel['patch'] or '/')}</code>\n"
        f"👤 Username: <code>{esc(panel['username'])}</code>\n"
        f"🔑 Password: <code>{'*' * min(len(panel['password']), 8)}</code>\n"
        f"Status: {'🟢 Active' if panel['is_active'] else '🔴 Inactive'}",
        kb)
