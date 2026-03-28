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
SNIPER_DB = os.path.join(SNIPER_DIR, "sniper.db")
OUTPUT_DIR = os.path.expanduser(
    "~/.openclaw/agents/architect/projects/investment-bot/buyorders")

# Ужесточённые пороги антибуста для buy orders
ANTIBOOST_TREND_THRESHOLD = -10.0    # med7d vs med30d (было -20%)
ANTIBOOST_VELOCITY_THRESHOLD = -5.0  # latest vs med7d (было -7%)
ANTIBOOST_MIN_SOLD_24H = 20          # минимум ликвидность (было 5)


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


# Кэш MarketCSGO bulk
_mcsgo_cache: set = set()
_mcsgo_cache_ts: float = 0
_MCSGO_CACHE_TTL = 3600  # 1 час


def _get_marketcsgo_names() -> set:
    """Загрузить названия предметов с MarketCSGO через bulk API (кэш 1ч)."""
    global _mcsgo_cache, _mcsgo_cache_ts
    
    if _mcsgo_cache and (time.time() - _mcsgo_cache_ts < _MCSGO_CACHE_TTL):
        log.info("MarketCSGO: из кэша (%d названий)", len(_mcsgo_cache))
        return _mcsgo_cache
    
    import httpx
    url = "https://market.csgo.com/api/v2/prices/class_instance/USD.json"
    
    try:
        log.info("MarketCSGO: загрузка bulk API...")
        r = httpx.get(url, timeout=60)
        data = r.json()
        
        if not data.get("success"):
            log.error("MarketCSGO bulk: success=false")
            return _mcsgo_cache or set()
        
        items = data.get("items", {})
        names = set()
        for key, val in items.items():
            name = val.get("market_hash_name", "")
            if name:
                names.add(name)
        
        _mcsgo_cache = names
        _mcsgo_cache_ts = time.time()
        log.info("MarketCSGO: загружено %d уникальных названий (bulk API)", len(names))
        return names
    except Exception as e:
        log.error("MarketCSGO bulk error: %s", e)
        # Fallback: sniper.db
        return _get_marketcsgo_names_fallback()


def _get_marketcsgo_names_fallback() -> set:
    """Fallback: загрузить из sniper.db если API недоступен."""
    import sqlite3
    if not os.path.exists(SNIPER_DB):
        return set()
    try:
        conn = sqlite3.connect(SNIPER_DB, timeout=10)
        rows = conn.execute(
            "SELECT name FROM zakup_items WHERE marketplace='MarketCSGO'"
        ).fetchall()
        conn.close()
        names = {r[0] for r in rows}
        log.info("MarketCSGO fallback: %d названий из sniper.db", len(names))
        return names
    except Exception as e:
        log.error("MarketCSGO fallback error: %s", e)
        return set()


def _antiboost_check(item: dict) -> tuple[bool, list[str]]:
    """Ужесточённый антибуст для buy orders.
    
    Пороги строже чем в lis-sniper:
    - Тренд: -10% (было -20%)
    - Velocity: -5% (было -7%)
    - Ликвидность: 20 sold/24h (было 5)
    """
    import statistics
    
    med30 = item.get("pricemedian30d") or 0
    med7 = item.get("pricemedian7d") or 0
    latest_sell = item.get("pricelatestsell") or 0
    latest_list = item.get("pricelatest") or 0
    pmin = item.get("pricemin") or 0
    pmax = item.get("pricemax") or 0
    sold24h = item.get("sold24h") or 0
    unstable = item.get("unstable", False)
    sales = item.get("latest10steamsales") or item.get("latest10") or []
    
    reasons = []
    
    # 1. Ликвидность (ужесточена: 20 вместо 5)
    if sold24h < ANTIBOOST_MIN_SOLD_24H:
        reasons.append(f"Ликвидность: {sold24h}/{ANTIBOOST_MIN_SOLD_24H}")
    
    # 2. Рост 30д
    if med30 > 0 and latest_sell > 0:
        growth = (latest_sell - med30) / med30 * 100
        if growth > 50:
            reasons.append(f"Хайп: {growth:+.1f}%")
        elif growth < -20:
            reasons.append(f"Дамп: {growth:+.1f}%")
    
    # 3. Медиана vs текущий
    if med30 > 0 and latest_list > 0:
        dev = (latest_list - med30) / med30 * 100
        if dev > 35:
            reasons.append(f"Хайп vs медиана: {dev:+.1f}%")
        elif dev < -20:
            reasons.append(f"Дамп vs медиана: {dev:+.1f}%")
    
    # 4. Волатильность
    if pmin > 0 and pmax > 0:
        vol = (pmax - pmin) / pmin * 100
        if vol > 150:
            reasons.append(f"Волатильность: {vol:.0f}%")
    
    # 5. Скачок из latest10
    if sales and len(sales) >= 6:
        try:
            prices = [float(s[1]) for s in sales if len(s) >= 2]
            if len(prices) >= 6:
                avg_recent = statistics.mean(prices[:3])
                avg_older = statistics.mean(prices[3:])
                if avg_older > 0:
                    spike = (avg_recent - avg_older) / avg_older * 100
                    if abs(spike) > 10:
                        reasons.append(f"Скачок: {spike:+.1f}%")
        except (ValueError, TypeError):
            pass
    
    # 6. Тренд: med7d vs med30d (ужесточен: -10% вместо -20%)
    if med7 > 0 and med30 > 0:
        trend = (med7 - med30) / med30 * 100
        if trend < ANTIBOOST_TREND_THRESHOLD:
            reasons.append(f"Тренд: {trend:+.1f}%")
    
    # 7. Velocity: latest vs med7d (ужесточен: -5% вместо -7%)
    if med7 > 0 and latest_sell > 0:
        velocity = (latest_sell - med7) / med7 * 100
        if velocity < ANTIBOOST_VELOCITY_THRESHOLD:
            reasons.append(f"Velocity: {velocity:+.1f}%")
    
    # 8. Unstable
    if unstable:
        reasons.append("Unstable")
    
    passed = len(reasons) == 0
    return passed, reasons


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
    """Отобрать и отсортировать предметы с антибустом и проверкой MarketCSGO."""
    results = []
    
    # Загружаем названия MarketCSGO для проверки ликвидности
    mcsgo_names = _get_marketcsgo_names()
    
    stats = {"total": 0, "no_buy": 0, "no_steam": 0, "low_sold": 0,
             "price_filter": 0, "excluded_cat": 0, "no_mcsgo": 0,
             "antiboost": 0, "no_margin": 0, "passed": 0}

    for item in raw_items:
        name = item.get("markethashname", "")
        if not name:
            continue
        stats["total"] += 1

        buy_order = item.get("buyorderprice") or 0
        if buy_order <= 0:
            stats["no_buy"] += 1
            continue

        steam_price = item.get("pricemedian30d") or 0
        if steam_price <= 0:
            stats["no_steam"] += 1
            continue

        sold24h = item.get("sold24h") or 0
        if sold24h < ANTIBOOST_MIN_SOLD_24H:
            stats["low_sold"] += 1
            continue

        # Фильтр по цене
        if buy_order < min_price or buy_order > max_price:
            stats["price_filter"] += 1
            continue

        # Исключения по категориям
        if _should_exclude(name, excluded):
            stats["excluded_cat"] += 1
            continue

        # ✅ Проверка наличия на MarketCSGO
        if mcsgo_names and name not in mcsgo_names:
            stats["no_mcsgo"] += 1
            continue

        # ✅ Антибуст (ужесточённый)
        ab_passed, ab_reasons = _antiboost_check(item)
        if not ab_passed:
            stats["antiboost"] += 1
            continue

        # Маржа
        net = steam_price * (1 - MARKET_FEE)
        margin = ((net - buy_order) / buy_order * 100) if buy_order > 0 else 0
        if margin <= 0:
            stats["no_margin"] += 1
            continue

        steam_url = (f"https://steamcommunity.com/market/listings/"
                     f"{APP_ID}/{quote(name)}")

        stats["passed"] += 1
        results.append({
            "name": name,
            "buy_order": round(buy_order, 2),
            "steam_price": round(steam_price, 2),
            "net": round(net, 2),
            "margin": round(margin, 1),
            "volume": sold24h,
            "url": steam_url,
        })

    # Логируем статистику фильтрации
    log.info("📊 Фильтрация: всего=%d, нет buy=%d, нет steam=%d, "
             "мало sold=%d, цена=%d, категория=%d, нет MCS=%d, "
             "антибуст=%d, нет маржи=%d → прошли=%d",
             stats["total"], stats["no_buy"], stats["no_steam"],
             stats["low_sold"], stats["price_filter"], stats["excluded_cat"],
             stats["no_mcsgo"], stats["antiboost"], stats["no_margin"],
             stats["passed"])
    
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
            f"📦 Предметов: {len(items)} (из {len(raw)} загруженных)\n"
            f"💰 Общая стоимость: ${total_cost:.2f}\n"
            f"📈 Средняя маржа: {avg_margin:.1f}%\n"
            f"📊 Топ маржа: {items[0]['margin']:.1f}% ({items[0]['name'][:30]})\n\n"
            f"🛡 Антибуст: тренд ≥{ANTIBOOST_TREND_THRESHOLD}%, "
            f"velocity ≥{ANTIBOOST_VELOCITY_THRESHOLD}%\n"
            f"📈 Мин. ликвидность: {ANTIBOOST_MIN_SOLD_24H} sold/24ч\n"
            f"✅ Проверено на MarketCSGO",
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
