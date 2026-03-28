"""Investment Bot — Генерация Excel buy orders для STM-MCS.

ConversationHandler:
1. Объём ($) — ввод числа
2. Исключения — toggle кнопки
3. Мин. цена — ввод числа
4. Макс. цена — ввод числа
→ Генерация Excel → отправка файлом
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from telegram import (Update, InlineKeyboardButton,
                      InlineKeyboardMarkup)
from telegram.ext import (ContextTypes, ConversationHandler,
                          CallbackQueryHandler, MessageHandler,
                          filters)

log = logging.getLogger("invest")

# States
ST_VOLUME, ST_EXCLUDES, ST_MIN_PRICE, ST_MAX_PRICE = range(4)

# Категории для исключения
CATEGORIES = [
    ("knives",    "🔪 Ножи/перчатки", ["★"]),
    ("cases",     "📦 Кейсы",         ["Case"]),
    ("capsules",  "💊 Капсулы",       ["Capsule"]),
    ("stickers",  "🏷️ Стикеры",      ["Sticker"]),
    ("graffiti",  "🎨 Граффити",      ["Graffiti", "Sealed Graffiti"]),
    ("music",     "🎵 Музыка",        ["Music Kit"]),
    ("patches",   "🎖️ Патчи",        ["Patch"]),
    ("souvenir",  "🔫 Сувенирное",    ["Souvenir"]),
    ("agents",    "📜 Агенты",        ["Agent"]),
    ("pins",      "🏅 Медали/пины",   ["Pin", "Medal"]),
]

APP_ID = 730
MARKET_FEE = 0.10

SNIPER_DIR = os.path.expanduser(
    "~/.openclaw/agents/architect/projects/lis-sniper")
OUTPUT_DIR = os.path.expanduser(
    "~/.openclaw/agents/architect/projects/investment-bot/buyorders")


def _excludes_kb(excluded: set) -> InlineKeyboardMarkup:
    """Клавиатура toggle-кнопок для исключений."""
    rows = []
    row = []
    for key, label, _ in CATEGORIES:
        mark = "❌" if key in excluded else "✅"
        row.append(InlineKeyboardButton(
            f"{mark} {label}", callback_data=f"bo:ex:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        "✅ Готово", callback_data="bo:ex:done")])
    return InlineKeyboardMarkup(rows)


def _get_steamwebapi_data() -> list:
    """Получить bulk данные из SteamWebAPI."""
    _src = os.path.join(SNIPER_DIR, "src")
    if _src not in sys.path:
        sys.path.insert(0, _src)
    saved_cwd = os.getcwd()
    os.chdir(SNIPER_DIR)
    try:
        import importlib
        # Загружаем .env lis-sniper для ключа
        env_path = os.path.join(SNIPER_DIR, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        
        import steamwebapi
        key = os.environ.get("STEAMWEBAPI_KEY", "")
        if key:
            steamwebapi.set_key(key)
        items = steamwebapi.get_bulk("cs2", max_items=50000)
        log.info("SteamWebAPI: %d предметов загружено", len(items))
        return items
    except Exception as e:
        log.error("SteamWebAPI error: %s", e)
        return []
    finally:
        os.chdir(saved_cwd)


def _should_exclude(name: str, excluded: set) -> bool:
    """Проверить нужно ли исключить предмет."""
    for key, _, patterns in CATEGORIES:
        if key not in excluded:
            continue
        for pattern in patterns:
            if pattern in name:
                return True
    return False


def _build_items(raw_items: list, excluded: set,
                 min_price: float, max_price: float,
                 total_volume: float) -> list:
    """Отобрать и отсортировать предметы."""
    results = []

    for item in raw_items:
        name = item.get("markethashname", "")
        if not name:
            continue

        buy_order = item.get("buyorderprice") or 0
        if buy_order <= 0:
            continue

        steam_price = item.get("pricemedian30d") or 0
        if steam_price <= 0:
            continue

        sold24h = item.get("sold24h") or 0
        if sold24h < 5:
            continue

        # Фильтр по цене
        if buy_order < min_price or buy_order > max_price:
            continue

        # Исключения
        if _should_exclude(name, excluded):
            continue

        # Маржа
        net = steam_price * (1 - MARKET_FEE)
        margin = ((net - buy_order) / buy_order * 100) if buy_order > 0 else 0
        if margin <= 0:
            continue

        steam_url = (f"https://steamcommunity.com/market/listings/"
                     f"{APP_ID}/{quote(name)}")

        results.append({
            "name": name,
            "buy_order": round(buy_order, 2),
            "steam_price": round(steam_price, 2),
            "net": round(net, 2),
            "margin": round(margin, 1),
            "volume": sold24h,
            "url": steam_url,
        })

    # Сортировка по марже
    results.sort(key=lambda x: x["margin"], reverse=True)

    # Распределение количества ордеров
    if results and total_volume > 0:
        # Равномерное распределение по топ предметам
        budget_left = total_volume
        for item in results:
            qty = max(1, int(budget_left / item["buy_order"] / len(results)))
            if qty * item["buy_order"] > budget_left:
                qty = max(1, int(budget_left / item["buy_order"]))
            item["qty"] = qty

        # Второй проход: заполняем бюджет сверху вниз
        budget_left = total_volume
        for item in results:
            max_qty = int(budget_left / item["buy_order"]) if item["buy_order"] > 0 else 0
            item["qty"] = min(max_qty, max(1, item.get("qty", 1)))
            budget_left -= item["qty"] * item["buy_order"]
            if budget_left <= 0:
                item["qty"] = max(item["qty"], 0)
                break
        
        # Убираем предметы с qty=0
        results = [r for r in results if r.get("qty", 0) > 0]

    log.info("Отобрано: %d предметов", len(results))
    return results


def _generate_excel(items: list, params: dict) -> str:
    """Создать Excel файл."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"buyorders_{today}.xlsx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Buy Orders"

    # Стили
    hdr_font = Font(bold=True, color="FFFFFF", size=11)
    hdr_fill = PatternFill(start_color="2F5496", end_color="2F5496",
                           fill_type="solid")
    hdr_align = Alignment(horizontal="center", vertical="center")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"))

    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE",
                             fill_type="solid")
    yellow_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C",
                              fill_type="solid")

    # Информационная строка
    ws.merge_cells("A1:F1")
    info = (f"Объём: ${params['volume']:.2f} | "
            f"Цена: ${params['min_price']:.2f}-${params['max_price']:.2f} | "
            f"Предметов: {len(items)} | "
            f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    ws["A1"] = info
    ws["A1"].font = Font(bold=True, size=10)

    # Заголовки
    headers = ["№", "App ID", "Название", "Ссылка Steam",
               "Buy Order ($)", "Кол-во", "Маржа %", "Объём 24ч"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=header)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = hdr_align
        cell.border = border

    # Данные
    total_cost = 0
    for i, item in enumerate(items, 1):
        row = i + 2
        ws.cell(row=row, column=1, value=i).border = border
        ws.cell(row=row, column=2, value=APP_ID).border = border
        ws.cell(row=row, column=3, value=item["name"]).border = border

        url_cell = ws.cell(row=row, column=4, value=item["url"])
        url_cell.hyperlink = item["url"]
        url_cell.font = Font(color="0563C1", underline="single")
        url_cell.border = border

        ws.cell(row=row, column=5, value=item["buy_order"]).border = border
        ws.cell(row=row, column=6, value=item.get("qty", 1)).border = border

        margin_cell = ws.cell(row=row, column=7, value=item["margin"])
        margin_cell.border = border
        if item["margin"] >= 20:
            margin_cell.fill = green_fill
        elif item["margin"] >= 10:
            margin_cell.fill = yellow_fill

        ws.cell(row=row, column=8, value=item["volume"]).border = border

        total_cost += item["buy_order"] * item.get("qty", 1)

    # Итого
    total_row = len(items) + 3
    ws.cell(row=total_row, column=4, value="ИТОГО:").font = Font(bold=True)
    ws.cell(row=total_row, column=5, value=round(total_cost, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=6,
            value=sum(it.get("qty", 1) for it in items)).font = Font(bold=True)

    # Ширина колонок
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 45
    ws.column_dimensions["D"].width = 55
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 8
    ws.column_dimensions["G"].width = 10
    ws.column_dimensions["H"].width = 10

    wb.save(filepath)
    log.info("Excel сохранён: %s (%d предметов, $%.2f)",
             filepath, len(items), total_cost)
    return filepath


# ============================================================
# ConversationHandler
# ============================================================

async def start_buyorders(update: Update,
                          ctx: ContextTypes.DEFAULT_TYPE):
    """Начало — спрашиваем объём."""
    ctx.user_data["bo_excludes"] = set()
    await update.message.reply_text(
        "📊 <b>Создание БД STM-MCS</b>\n\n"
        "Введи общий объём ордеров ($):\n"
        "<i>Например: 100</i>",
        parse_mode="HTML")
    return ST_VOLUME


async def got_volume(update: Update,
                     ctx: ContextTypes.DEFAULT_TYPE):
    """Получили объём → показываем исключения."""
    text = update.message.text.strip().replace("$", "").replace(",", ".")
    try:
        volume = float(text)
        if volume <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи число > 0")
        return ST_VOLUME

    ctx.user_data["bo_volume"] = volume
    excluded = ctx.user_data.get("bo_excludes", set())

    await update.message.reply_text(
        f"✅ Объём: <b>${volume:.2f}</b>\n\n"
        f"Выбери категории для <b>исключения</b>:\n"
        f"(нажми чтобы переключить, потом «Готово»)",
        parse_mode="HTML",
        reply_markup=_excludes_kb(excluded))
    return ST_EXCLUDES


async def toggle_exclude(update: Update,
                         ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle категории исключения."""
    q = update.callback_query
    await q.answer()
    data = q.data  # bo:ex:knives или bo:ex:done

    key = data.split(":")[-1]

    if key == "done":
        excluded = ctx.user_data.get("bo_excludes", set())
        excl_text = ", ".join(
            label for k, label, _ in CATEGORIES if k in excluded
        ) or "ничего"
        await q.message.edit_text(
            f"✅ Исключено: {excl_text}\n\n"
            f"Введи <b>минимальную</b> цену ордера ($):\n"
            f"<i>Например: 0.20</i>",
            parse_mode="HTML")
        return ST_MIN_PRICE

    excluded = ctx.user_data.get("bo_excludes", set())
    if key in excluded:
        excluded.discard(key)
    else:
        excluded.add(key)
    ctx.user_data["bo_excludes"] = excluded

    await q.message.edit_reply_markup(
        reply_markup=_excludes_kb(excluded))
    return ST_EXCLUDES


async def got_min_price(update: Update,
                        ctx: ContextTypes.DEFAULT_TYPE):
    """Получили мин. цену → спрашиваем макс."""
    text = update.message.text.strip().replace("$", "").replace(",", ".")
    try:
        min_price = float(text)
        if min_price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи число ≥ 0")
        return ST_MIN_PRICE

    ctx.user_data["bo_min_price"] = min_price

    await update.message.reply_text(
        f"✅ Мин. цена: <b>${min_price:.2f}</b>\n\n"
        f"Введи <b>максимальную</b> цену ордера ($):\n"
        f"<i>Например: 10.00</i>",
        parse_mode="HTML")
    return ST_MAX_PRICE


async def got_max_price(update: Update,
                        ctx: ContextTypes.DEFAULT_TYPE):
    """Получили макс. цену → генерируем Excel."""
    text = update.message.text.strip().replace("$", "").replace(",", ".")
    try:
        max_price = float(text)
        if max_price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи число > 0")
        return ST_MAX_PRICE

    min_price = ctx.user_data.get("bo_min_price", 0)
    if max_price <= min_price:
        await update.message.reply_text(
            f"❌ Макс. цена должна быть > мин. (${min_price:.2f})")
        return ST_MAX_PRICE

    volume = ctx.user_data.get("bo_volume", 100)
    excluded = ctx.user_data.get("bo_excludes", set())

    params = {
        "volume": volume,
        "min_price": min_price,
        "max_price": max_price,
        "excluded": list(excluded),
    }

    # Отправляем "генерирую..."
    msg = await update.message.reply_text(
        f"⏳ <b>Генерирую БД STM-MCS...</b>\n\n"
        f"💰 Объём: ${volume:.2f}\n"
        f"📊 Цена: ${min_price:.2f} — ${max_price:.2f}\n"
        f"🚫 Исключено: {len(excluded)} категорий",
        parse_mode="HTML")

    try:
        # Загружаем данные
        raw = _get_steamwebapi_data()
        if not raw:
            await msg.edit_text("❌ Не удалось загрузить данные SteamWebAPI")
            ctx.user_data.clear()
            return ConversationHandler.END

        items = _build_items(raw, excluded, min_price, max_price, volume)
        if not items:
            await msg.edit_text("❌ Нет предметов по заданным параметрам")
            ctx.user_data.clear()
            return ConversationHandler.END

        filepath = _generate_excel(items, params)

        total_cost = sum(it["buy_order"] * it.get("qty", 1) for it in items)
        avg_margin = sum(it["margin"] for it in items) / len(items)

        await msg.edit_text(
            f"✅ <b>БД STM-MCS готова!</b>\n\n"
            f"📦 Предметов: {len(items)}\n"
            f"💰 Общая стоимость: ${total_cost:.2f}\n"
            f"📈 Средняя маржа: {avg_margin:.1f}%\n"
            f"📊 Топ маржа: {items[0]['margin']:.1f}% ({items[0]['name'][:30]})",
            parse_mode="HTML")

        # Отправляем файл
        with open(filepath, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(filepath),
                caption=f"📊 Buy Orders | ${volume:.2f} | "
                        f"{len(items)} предметов")

    except Exception as e:
        log.error("Buyorders generation error: %s", e)
        await msg.edit_text(f"❌ Ошибка генерации: {e}")

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_buyorders(update: Update,
                           ctx: ContextTypes.DEFAULT_TYPE):
    """Отмена."""
    ctx.user_data.clear()
    await update.message.reply_text("❌ Генерация отменена")
    return ConversationHandler.END


def get_conversation_handler() -> ConversationHandler:
    """Создать ConversationHandler для buy orders."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^📊 Создать БД STM-MCS$"),
                start_buyorders),
        ],
        states={
            ST_VOLUME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               got_volume),
            ],
            ST_EXCLUDES: [
                CallbackQueryHandler(toggle_exclude,
                                     pattern=r"^bo:ex:"),
            ],
            ST_MIN_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               got_min_price),
            ],
            ST_MAX_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               got_max_price),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(r"^/cancel$"),
                           cancel_buyorders),
        ],
        per_message=False,
    )
