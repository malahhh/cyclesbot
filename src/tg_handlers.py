"""Investment Bot — Telegram UI.

Главное меню: 📊 Инвестиции | 🔄 Круги | 📜 История
"""

import asyncio
import logging

from telegram import (Update, InlineKeyboardButton,
                      InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, KeyboardButton)
from telegram.ext import (ContextTypes, CommandHandler,
                          CallbackQueryHandler, MessageHandler,
                          filters)

import db
import dashboard
from config import AUTHORIZED_USER

log = logging.getLogger("invest")

STATUS_EMOJI = {"buy": "🟢", "hold": "🟡", "sale": "🟠", "done": "✅"}


def _auth(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid == AUTHORIZED_USER


def _main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📊 Инвестиции"),
          KeyboardButton("🔄 Круги")],
         [KeyboardButton("📜 История")]],
        resize_keyboard=True)


def _invest_kb(page: int = 0) -> InlineKeyboardMarkup:
    total = dashboard.invest_pages()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "◀️", callback_data=f"inv:p:{page - 1}"))
    nav.append(InlineKeyboardButton(
        f"{page + 1}/{total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(
            "▶️", callback_data=f"inv:p:{page + 1}"))
    rows = []
    if total > 1:
        rows.append(nav)
    # Кнопки обновления для каждого аккаунта
    accs = db.get_invest_accounts()
    if accs:
        refresh_row = [InlineKeyboardButton(
            f"🔄 {a['login']}", callback_data=f"inv:ref:{a['id']}")
            for a in accs]
        rows.append(refresh_row)
    rows.extend([
        [InlineKeyboardButton("➕ Добавить",
                              callback_data="inv:add"),
         InlineKeyboardButton("🗑 Удалить",
                              callback_data="inv:del_pick")],
        [InlineKeyboardButton("🔙 Назад",
                              callback_data="back")],
    ])
    return InlineKeyboardMarkup(rows)


def _circles_kb() -> InlineKeyboardMarkup:
    accs = db.get_circle_accounts()
    active = [a for a in accs
              if a["status"] in ("buy", "hold", "sale")]
    rows = []
    # Каждый аккаунт — кликабельная кнопка
    for a in active:
        emoji = STATUS_EMOJI.get(a["status"], "⚪")
        rows.append([InlineKeyboardButton(
            f"{emoji} {a['login']} | {a['amount']}",
            callback_data=f"cir:view:{a['id']}")])
    rows.append([InlineKeyboardButton("➕ Добавить круг",
                                      callback_data="cir:add")])
    return InlineKeyboardMarkup(rows)


# ============================================================
# /start
# ============================================================
async def cmd_start(update: Update,
                    ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    ctx.user_data.clear()
    inv_accs = db.get_invest_accounts()
    cir_accs = db.get_circle_accounts()
    active = len([a for a in cir_accs
                  if a["status"] in ("buy", "hold")])
    done = len([a for a in cir_accs if a["status"] == "done"])
    await update.message.reply_text(
        f"📊 <b>Investment Bot</b>\n\n"
        f"Инвестиции: {len(inv_accs)} акк\n"
        f"Активных кругов: {active}\n"
        f"Завершённых: {done}",
        parse_mode="HTML", reply_markup=_main_kb())


# ============================================================
# Callback dispatch
# ============================================================
async def on_callback(update: Update,
                      ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("⛔")
        return
    await q.answer()
    data = q.data

    # --- Главное меню ---
    if data == "sec:invest":
        await q.message.edit_text(
            dashboard.invest_text(0), parse_mode="HTML",
            reply_markup=_invest_kb(0))

    elif data.startswith("inv:p:"):
        page = int(data.split(":")[2])
        await q.message.edit_text(
            dashboard.invest_text(page), parse_mode="HTML",
            reply_markup=_invest_kb(page))

    elif data == "noop":
        pass

    elif data == "sec:circles":
        await q.message.edit_text(
            dashboard.circles_text(), parse_mode="HTML",
            reply_markup=_circles_kb())

    elif data == "sec:history":
        await q.message.edit_text(
            dashboard.history_text(), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад",
                                     callback_data="back")]]))

    elif data == "back":
        inv_accs = db.get_invest_accounts()
        cir_accs = db.get_circle_accounts()
        active = len([a for a in cir_accs
                      if a["status"] in ("buy", "hold")])
        done = len([a for a in cir_accs if a["status"] == "done"])
        await q.message.reply_text(
            f"📊 <b>Investment Bot</b>\n\n"
            f"Инвестиции: {len(inv_accs)} акк\n"
            f"Активных кругов: {active}\n"
            f"Завершённых: {done}",
            parse_mode="HTML", reply_markup=_main_kb())

    # --- Инвестиции ---
    elif data == "inv:add":
        ctx.user_data["flow"] = "inv_add"
        ctx.user_data["step"] = "login"
        await q.message.edit_text("Введи имя аккаунта:")

    elif data == "inv:del_pick":
        accs = db.get_invest_accounts()
        rows = [[InlineKeyboardButton(
            f"🗑 {a['login']}", callback_data=f"inv:del:{a['id']}")]
            for a in accs]
        rows.append([InlineKeyboardButton(
            "🔙", callback_data="sec:invest")])
        await q.message.edit_text(
            "Удалить:", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("inv:del:"):
        acc_id = int(data.split(":")[2])
        acc = db.get_invest_account(acc_id)
        if acc:
            db.delete_invest_account(acc_id)
        await q.message.reply_text(
            f"✅ Удалён: {acc['login'] if acc else '?'}",
            reply_markup=_main_kb())

    elif data.startswith("inv:ref:"):
        acc_id = int(data.split(":")[2])
        acc = db.get_invest_account(acc_id)
        if not acc:
            return
        await q.message.edit_text(
            f"🔄 Обновляю <b>{acc['login']}</b>...",
            parse_mode="HTML")
        chat_id = q.message.chat_id
        msg_id = q.message.message_id
        bot = ctx.bot
        loop = asyncio.get_event_loop()

        def _do_one(a=acc):
            import daemon
            daemon.update_steam_account(a["steam_id"], a["login"])
            text = dashboard.invest_text(0)
            asyncio.run_coroutine_threadsafe(
                bot.edit_message_text(
                    text, chat_id=chat_id, message_id=msg_id,
                    parse_mode="HTML",
                    reply_markup=_invest_kb(0)),
                loop)

        import threading
        threading.Thread(target=_do_one, daemon=True).start()

    # --- Круги: добавить ---
    elif data == "cir:add":
        ctx.user_data["flow"] = "cir_add"
        ctx.user_data["step"] = "login"
        await q.message.edit_text(
            "Введи имя аккаунта для нового круга:")

    # --- Круги: просмотр аккаунта ---
    elif data.startswith("cir:view:"):
        acc_id = int(data.split(":")[2])
        acc = db.get_circle_account(acc_id)
        if not acc:
            return
        emoji = STATUS_EMOJI.get(acc["status"], "⚪")
        # Инвентарь
        inv_parts = []
        for app_id, game in [(730, "CS2"), (570, "Dota2")]:
            inv = db.get_inventory(acc["steam_id"], app_id)
            if inv and inv["items_count"] > 0:
                inv_parts.append(
                    f"{game}: {inv['items_count']} шт "
                    f"(${inv['total_value']:.2f})")
        inv_line = " | ".join(inv_parts) if inv_parts else "нет данных"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Сумма",
                                  callback_data=f"ef:{acc_id}:amount"),
             InlineKeyboardButton("🔄 Схема",
                                  callback_data=f"ef:{acc_id}:scheme")],
            [InlineKeyboardButton("📋 Статус",
                                  callback_data=f"ef:{acc_id}:status"),
             InlineKeyboardButton("📝 Заметка",
                                  callback_data=f"ef:{acc_id}:check_note")],
            [InlineKeyboardButton("🔄 Обновить инвентарь",
                                  callback_data=f"cir:ref:{acc_id}")],
            [InlineKeyboardButton("✅ Завершить круг",
                                  callback_data=f"cir:fin:{acc_id}")],
            [InlineKeyboardButton("🔙 Назад",
                                  callback_data="sec:circles")],
        ])
        await q.message.edit_text(
            f"{emoji} <b>{acc['login']}</b>\n\n"
            f"💰 Сумма: {acc['amount']}\n"
            f"🔄 Схема: {acc['scheme']}\n"
            f"📦 Инвентарь: {inv_line}\n"
            f"📋 Заметка: {acc['check_note'] or '—'}\n"
            f"📊 Статус: {acc['status']}",
            parse_mode="HTML", reply_markup=kb)

    elif data.startswith("cir:ref:"):
        acc_id = int(data.split(":")[2])
        acc = db.get_circle_account(acc_id)
        if not acc:
            return
        await q.message.edit_text(
            f"🔄 Обновляю <b>{acc['login']}</b>...",
            parse_mode="HTML")
        chat_id = q.message.chat_id
        msg_id = q.message.message_id
        bot = ctx.bot
        loop = asyncio.get_event_loop()

        def _do_cir(a=acc):
            import daemon
            daemon.update_steam_account(a["steam_id"], a["login"])
            # Показать обновлённую карточку
            inv_parts = []
            for app_id, game in [(730, "CS2"), (570, "Dota2")]:
                inv = db.get_inventory(a["steam_id"], app_id)
                if inv and inv["items_count"] > 0:
                    inv_parts.append(
                        f"{game}: {inv['items_count']} шт "
                        f"(${inv['total_value']:.2f})")
            inv_line = " | ".join(inv_parts) if inv_parts else "—"
            e = STATUS_EMOJI.get(a["status"], "⚪")
            text = (
                f"{e} <b>{a['login']}</b>\n\n"
                f"💰 Сумма: {a['amount']}\n"
                f"🔄 Схема: {a['scheme']}\n"
                f"📦 Инвентарь: {inv_line}\n"
                f"📋 Заметка: {a['check_note'] or '—'}\n"
                f"📊 Статус: {a['status']}\n\n"
                f"✅ Инвентарь обновлён")
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад",
                                     callback_data="sec:circles")]])
            asyncio.run_coroutine_threadsafe(
                bot.edit_message_text(
                    text, chat_id=chat_id, message_id=msg_id,
                    parse_mode="HTML", reply_markup=kb), loop)

        import threading
        threading.Thread(target=_do_cir, daemon=True).start()

    elif data.startswith("cir:fin:"):
        acc_id = int(data.split(":")[2])
        ctx.user_data["flow"] = "cir_finish"
        ctx.user_data["finish_acc"] = acc_id
        acc = db.get_circle_account(acc_id)
        await q.message.edit_text(
            f"✅ <b>{acc['login']}</b>\n"
            f"Вложено: {acc['amount']}\n\n"
            f"Введи сумму вывода ($):",
            parse_mode="HTML")

    elif data.startswith("ef:"):
        parts = data.split(":")
        acc_id, field = int(parts[1]), parts[2]
        ctx.user_data["flow"] = "edit"
        ctx.user_data["edit_acc"] = acc_id
        ctx.user_data["edit_field"] = field
        if field == "status":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 buy",
                                      callback_data="sv:buy"),
                 InlineKeyboardButton("🟡 hold",
                                      callback_data="sv:hold")],
                [InlineKeyboardButton("🟠 sale",
                                      callback_data="sv:sale"),
                 InlineKeyboardButton("✅ done",
                                      callback_data="sv:done")],
            ])
            await q.message.edit_text("Статус:", reply_markup=kb)
        else:
            labels = {"amount": "сумму", "scheme": "схему",
                      "check_note": "заметку"}
            await q.message.edit_text(
                f"Введи {labels.get(field, field)}:")

    elif data.startswith("sv:"):
        value = data[3:]
        acc_id = ctx.user_data.get("edit_acc")
        field = ctx.user_data.get("edit_field", "status")
        ctx.user_data.clear()
        if acc_id:
            db.update_circle_account(acc_id, **{field: value})
        await q.message.reply_text(
            "✅ Обновлено", reply_markup=_main_kb())


# ============================================================
# Текстовый ввод
# ============================================================
async def handle_text(update: Update,
                      ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = update.message.text.strip()

    # --- Постоянные кнопки меню ---
    if text == "📊 Инвестиции":
        ctx.user_data.clear()
        await update.message.reply_text(
            dashboard.invest_text(0), parse_mode="HTML",
            reply_markup=_invest_kb(0))
        return
    elif text == "🔄 Круги":
        ctx.user_data.clear()
        await update.message.reply_text(
            dashboard.circles_text(), parse_mode="HTML",
            reply_markup=_circles_kb())
        return
    elif text == "📜 История":
        ctx.user_data.clear()
        await update.message.reply_text(
            dashboard.history_text(), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад",
                                     callback_data="back")]]))
        return

    flow = ctx.user_data.get("flow")
    step = ctx.user_data.get("step")

    # --- Инвестиции: добавить ---
    if flow == "inv_add":
        if step == "login":
            ctx.user_data["add_login"] = text
            ctx.user_data["step"] = "steamid"
            await update.message.reply_text("SteamID (76561...):")
        elif step == "steamid":
            login = ctx.user_data.pop("add_login", "")
            ctx.user_data.clear()
            if login and text:
                db.add_invest_account(login, text)
                await update.message.reply_text(
                    f"✅ Добавлен: {login}",
                    reply_markup=_main_kb())

    # --- Круги: добавить ---
    elif flow == "cir_add":
        if step == "login":
            ctx.user_data["add_login"] = text
            ctx.user_data["step"] = "steamid"
            await update.message.reply_text("SteamID:")
        elif step == "steamid":
            ctx.user_data["add_steamid"] = text
            ctx.user_data["step"] = "amount"
            await update.message.reply_text("Сумма закупа ($):")
        elif step == "amount":
            ctx.user_data["add_amount"] = text
            ctx.user_data["step"] = "scheme"
            await update.message.reply_text("Схема:")
        elif step == "scheme":
            login = ctx.user_data.pop("add_login", "")
            steam_id = ctx.user_data.pop("add_steamid", "")
            amount = ctx.user_data.pop("add_amount", "")
            scheme = text
            ctx.user_data.clear()
            if login and steam_id:
                db.add_circle_account(login, steam_id,
                                      amount=amount, scheme=scheme)
                await update.message.reply_text(
                    f"✅ Круг создан: {login} | {amount}",
                    reply_markup=_main_kb())

    # --- Круги: завершить ---
    elif flow == "cir_finish":
        acc_id = ctx.user_data.get("finish_acc")
        ctx.user_data.clear()
        if not acc_id:
            return
        acc = db.get_circle_account(acc_id)
        if not acc:
            return
        try:
            withdrawn = float(text.replace("$", "")
                              .replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Введи число")
            return
        try:
            invested = float(acc["amount"].replace("$", "")
                             .split("+")[0].strip())
        except (ValueError, IndexError):
            invested = 0
        profit = withdrawn - invested
        roi = (profit / invested * 100) if invested > 0 else 0
        emoji = "📈" if profit >= 0 else "📉"
        db.update_circle_account(
            acc_id, status="done",
            check_note=f"Вывод: ${withdrawn:.2f}, "
                       f"P/L: ${profit:+.2f} ({roi:+.1f}%)")
        await update.message.reply_text(
            f"✅ <b>Круг завершён: {acc['login']}</b>\n\n"
            f"💰 Вложено: ${invested:.2f}\n"
            f"💸 Выведено: ${withdrawn:.2f}\n"
            f"{emoji} P/L: <b>${profit:+.2f}</b> ({roi:+.1f}%)",
            parse_mode="HTML", reply_markup=_main_kb())

    # --- Edit field ---
    elif flow == "edit":
        acc_id = ctx.user_data.get("edit_acc")
        field = ctx.user_data.get("edit_field")
        ctx.user_data.clear()
        if acc_id and field:
            db.update_circle_account(acc_id, **{field: text})
            await update.message.reply_text(
                "✅ Обновлено", reply_markup=_main_kb())


def setup_handlers(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text))
