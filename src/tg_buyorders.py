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
ST_VOLUME, ST_EXCLUDES, ST_MIN_PRICE, ST_MAX_PRICE, ST_MIN_PROFIT = range(5)

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
ANTIBOOST_MIN_SOLD_24H = 12          # минимум ликвидность
MIN_AGE_DAYS = 180                   # минимум 6 месяцев на рынке

# MarketCSGO API
MCSGO_API_KEY = "Og13Tpq8R6ErI11h71n96YwKN4nEM4J"
MCSGO_BATCH_SIZE = 50                # макс предметов за запрос
MCSGO_RATE_LIMIT = 0.35              # ~3 req/sec (с запасом, лимит 4 req/sec)


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


# Кэш MarketCSGO bulk {name: price} — быстрая проверка наличия
_mcsgo_names_cache: set = set()
_mcsgo_names_ts: float = 0
_MCSGO_CACHE_TTL = 3600  # 1 час


def _get_marketcsgo_names() -> set:
    """Быстрая загрузка множества названий через bulk (для фильтрации)."""
    global _mcsgo_names_cache, _mcsgo_names_ts
    
    if _mcsgo_names_cache and (time.time() - _mcsgo_names_ts < _MCSGO_CACHE_TTL):
        log.info("MarketCSGO names: из кэша (%d)", len(_mcsgo_names_cache))
        return _mcsgo_names_cache
    
    import httpx
    try:
        log.info("MarketCSGO: загрузка bulk для списка названий...")
        r = httpx.get(
            "https://market.csgo.com/api/v2/prices/class_instance/USD.json",
            timeout=60)
        data = r.json()
        if data.get("success"):
            names = set()
            for val in data.get("items", {}).values():
                n = val.get("market_hash_name", "")
                if n:
                    names.add(n)
            _mcsgo_names_cache = names
            _mcsgo_names_ts = time.time()
            log.info("MarketCSGO: %d названий загружено", len(names))
            return names
    except Exception as e:
        log.error("MarketCSGO bulk names error: %s", e)
    return _mcsgo_names_cache or set()


def _get_mcsgo_ref_prices(names: list) -> dict:
    """Получить негативные ref-цены через MarketCSGO API (batch по 50).
    
    Для каждого предмета:
      ref_price = min(median_7d, median_30d, latest)
    
    Returns: {name: ref_price}
    """
    import httpx
    import statistics
    
    now = time.time()
    result = {}
    batches = [names[i:i + MCSGO_BATCH_SIZE]
               for i in range(0, len(names), MCSGO_BATCH_SIZE)]
    
    log.info("MarketCSGO API: %d предметов, %d батчей (по %d)",
             len(names), len(batches), MCSGO_BATCH_SIZE)
    
    for bi, batch in enumerate(batches):
        params = {"key": MCSGO_API_KEY}
        for name in batch:
            params.setdefault("list_hash_name[]", [])
            if isinstance(params["list_hash_name[]"], str):
                params["list_hash_name[]"] = [params["list_hash_name[]"]]
        
        # httpx нужен список для repeated params
        url = "https://market.csgo.com/api/v2/get-list-items-info"
        query_parts = [f"key={MCSGO_API_KEY}"]
        for name in batch:
            query_parts.append(f"list_hash_name[]={quote(name)}")
        full_url = url + "?" + "&".join(query_parts)
        
        try:
            r = httpx.get(full_url, timeout=30)
            data = r.json()
            
            if not data.get("success"):
                log.warning("MarketCSGO batch %d/%d: success=false",
                           bi + 1, len(batches))
                time.sleep(MCSGO_RATE_LIMIT)
                continue
            
            for name, info in data.get("data", {}).items():
                history = info.get("history", [])
                if not history:
                    continue
                
                all_prices = []
                for entry in history:
                    try:
                        all_prices.append(float(entry[1]))
                    except (ValueError, TypeError, IndexError):
                        pass
                
                if not all_prices:
                    continue
                
                latest_price = all_prices[0]
                
                # --- Фильтр: стабильность цены ---
                # stdev > 30% от медианы → исключить
                # НО: только если >3 продаж за пределами ±30% от медианы
                if len(all_prices) >= 10:
                    med_all = statistics.median(all_prices)
                    if med_all > 0:
                        outliers = sum(
                            1 for p in all_prices
                            if abs(p - med_all) / med_all > 0.30)
                        if outliers > 3:
                            stdev = statistics.stdev(all_prices)
                            if stdev / med_all > 0.30:
                                continue  # нестабильная цена
                
                # --- Фильтр: тренд на MCSGO ---
                # avg последних 10 vs avg первых 10
                # Если дешевеет >10% → исключить
                if len(all_prices) >= 20:
                    avg_last10 = statistics.mean(all_prices[:10])
                    avg_first10 = statistics.mean(all_prices[-10:])
                    if avg_first10 > 0:
                        trend = (avg_last10 - avg_first10) / avg_first10 * 100
                        if trend < -10:
                            continue  # предмет дешевеет
                
                # Фильтруем по периодам
                prices_7d = []
                prices_30d = []
                for entry in history:
                    try:
                        ts = int(entry[0])
                        price = float(entry[1])
                    except (ValueError, TypeError, IndexError):
                        continue
                    age = now - ts
                    if age <= 7 * 86400:
                        prices_7d.append(price)
                    if age <= 30 * 86400:
                        prices_30d.append(price)
                
                med7 = statistics.median(prices_7d) if prices_7d else latest_price
                med30 = statistics.median(prices_30d) if prices_30d else latest_price
                
                # Негативный сценарий: минимум из трёх
                ref_price = min(med7, med30, latest_price)
                if ref_price > 0:
                    result[name] = round(ref_price, 2)
                    
        except Exception as e:
            log.error("MarketCSGO batch %d/%d error: %s", bi + 1, len(batches), e)
        
        # Rate limit
        if bi < len(batches) - 1:
            time.sleep(MCSGO_RATE_LIMIT)
        
        # Прогресс каждые 20 батчей
        if (bi + 1) % 20 == 0:
            log.info("MarketCSGO API: %d/%d батчей (%d цен получено)",
                    bi + 1, len(batches), len(result))
    
    log.info("MarketCSGO API: готово, %d/%d цен получено",
             len(result), len(names))
    return result


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


def _is_too_young(item: dict) -> bool:
    """Проверить что предмет младше MIN_AGE_DAYS (6 месяцев)."""
    from datetime import datetime, timezone
    
    # firstseenat — ISO формат: "2018-08-22T23:00:00+00:00"
    first_seen = item.get("firstseenat", "")
    if not first_seen:
        return False  # нет данных — пропускаем фильтр
    
    try:
        if isinstance(first_seen, str):
            seen_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        else:
            return False
        
        age_days = (datetime.now(timezone.utc) - seen_dt).days
        return age_days < MIN_AGE_DAYS
    except Exception:
        return False


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
                 total_volume: float,
                 min_profit: float = 0,
                 progress_cb=None) -> list:
    """Отобрать и отсортировать предметы с антибустом и проверкой MarketCSGO.
    
    Двухфазная фильтрация:
    1. Быстрая фильтрация (SteamWebAPI + bulk names) → кандидаты
    2. Batch запрос ref-цен MarketCSGO API → финальный расчёт маржи
    """
    # Фаза 1: быстрая фильтрация
    mcsgo_names = _get_marketcsgo_names()
    
    stats = {"total": 0, "no_buy": 0, "no_steam": 0, "low_sold": 0,
             "price_filter": 0, "excluded_cat": 0, "no_mcsgo": 0,
             "too_young": 0, "antiboost": 0, "no_margin": 0,
             "no_ref_price": 0, "passed": 0}
    
    candidates = []  # прошли быстрые фильтры

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

        if buy_order < min_price or buy_order > max_price:
            stats["price_filter"] += 1
            continue

        if _should_exclude(name, excluded):
            stats["excluded_cat"] += 1
            continue

        if mcsgo_names and name not in mcsgo_names:
            stats["no_mcsgo"] += 1
            continue

        if _is_too_young(item):
            stats["too_young"] += 1
            continue

        ab_passed, _ = _antiboost_check(item)
        if not ab_passed:
            stats["antiboost"] += 1
            continue

        candidates.append({
            "name": name,
            "buy_order": buy_order,
            "steam_price": steam_price,
            "sold24h": sold24h,
            "item": item,
        })

    log.info("📊 Фаза 1: %d кандидатов из %d (нет buy=%d, нет steam=%d, "
             "sold=%d, цена=%d, кат=%d, MCS=%d, молод=%d, антибуст=%d)",
             len(candidates), stats["total"], stats["no_buy"],
             stats["no_steam"], stats["low_sold"], stats["price_filter"],
             stats["excluded_cat"], stats["no_mcsgo"], stats["too_young"],
             stats["antiboost"])

    if not candidates:
        return []

    # Фаза 2: получаем ref-цены MarketCSGO API (batch по 50)
    if progress_cb:
        progress_cb(f"⏳ Получаю цены MarketCSGO ({len(candidates)} предметов)...")
    
    candidate_names = [c["name"] for c in candidates]
    ref_prices = _get_mcsgo_ref_prices(candidate_names)
    
    # Фаза 3: финальный расчёт маржи от ref-цен
    if min_profit > 0:
        log.info("📈 Фильтр мин. прибыли: %.0f%%", min_profit)
    
    results = []
    for c in candidates:
        name = c["name"]
        buy_price = c["buy_order"]
        
        ref_price = ref_prices.get(name)
        if not ref_price or ref_price <= 0:
            stats["no_ref_price"] += 1
            continue
        
        net = ref_price * (1 - MARKET_FEE)
        margin = ((net - buy_price) / buy_price * 100) if buy_price > 0 else 0
        if margin < min_profit:
            stats["no_margin"] += 1
            continue

        steam_url = (f"https://steamcommunity.com/market/listings/"
                     f"{APP_ID}/{quote(name)}")
        mcsgo_url = f"https://market.csgo.com/?s=&search={quote(name)}"

        stats["passed"] += 1
        results.append({
            "name": name,
            "buy_order": round(buy_price, 2),
            "steam_price": round(c["steam_price"], 2),
            "mcsgo_price": round(ref_price, 2),
            "net": round(net, 2),
            "margin": round(margin, 1),
            "volume": c["sold24h"],
            "url": steam_url,
            "mcsgo_url": mcsgo_url,
        })

    log.info("📊 Фаза 2: ref-цены=%d/%d, нет маржи=%d → итого=%d",
             len(ref_prices), len(candidates),
             stats["no_margin"], stats["passed"])
    
    # Сортировка по марже
    results.sort(key=lambda x: x["margin"], reverse=True)

    # Каждый предмет по 1 штуке, обрезаем по бюджету
    budget_left = total_volume
    for item in results:
        item["qty"] = 1
    
    # Обрезаем по бюджету
    if total_volume > 0:
        filtered = []
        for item in results:
            if budget_left >= item["buy_order"]:
                filtered.append(item)
                budget_left -= item["buy_order"]
        results = filtered

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
               "Ссылка MarketCSGO", "Buy Order ($)", "Кол-во",
               "Маржа %", "Объём 24ч"]
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

        mcsgo_cell = ws.cell(row=row, column=5, value=item.get("mcsgo_url", ""))
        mcsgo_cell.hyperlink = item.get("mcsgo_url", "")
        mcsgo_cell.font = Font(color="0563C1", underline="single")
        mcsgo_cell.border = border

        ws.cell(row=row, column=6, value=item["buy_order"]).border = border
        ws.cell(row=row, column=7, value=item.get("qty", 1)).border = border

        margin_cell = ws.cell(row=row, column=8, value=item["margin"])
        margin_cell.border = border
        if item["margin"] >= 20:
            margin_cell.fill = green_fill
        elif item["margin"] >= 10:
            margin_cell.fill = yellow_fill

        ws.cell(row=row, column=9, value=item["volume"]).border = border

        total_cost += item["buy_order"] * item.get("qty", 1)

    # Итого
    total_row = len(items) + 3
    ws.cell(row=total_row, column=5, value="ИТОГО:").font = Font(bold=True)
    ws.cell(row=total_row, column=6, value=round(total_cost, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=7,
            value=sum(it.get("qty", 1) for it in items)).font = Font(bold=True)

    # Ширина колонок
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 45
    ws.column_dimensions["D"].width = 55
    ws.column_dimensions["E"].width = 55
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 8
    ws.column_dimensions["H"].width = 10
    ws.column_dimensions["I"].width = 10

    wb.save(filepath)
    log.info("Excel сохранён: %s (%d предметов, $%.2f)",
             filepath, len(items), total_cost)
    return filepath


# ============================================================
# ConversationHandler
# ============================================================

async def _cancel_and_menu(update: Update,
                           ctx: ContextTypes.DEFAULT_TYPE):
    """Выход из диалога при нажатии кнопки меню."""
    ctx.user_data.clear()
    # Передаём обработку в основной handle_text
    from tg_handlers import handle_text
    await handle_text(update, ctx)
    return ConversationHandler.END


async def start_buyorders(update: Update,
                          ctx: ContextTypes.DEFAULT_TYPE):
    """Начало — спрашиваем объём."""
    ctx.user_data["bo_excludes"] = {
        "knives", "cases", "capsules", "stickers",
        "graffiti", "patches", "agents", "pins"
    }
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
            f"<i>По умолчанию: 0.50</i>",
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
    if text in (".", "", "д", "ок"):
        min_price = 0.50  # дефолт
    else:
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
        f"<i>По умолчанию: 10.00</i>",
        parse_mode="HTML")
    return ST_MAX_PRICE


async def got_max_price(update: Update,
                        ctx: ContextTypes.DEFAULT_TYPE):
    """Получили макс. цену → спрашиваем скидку."""
    text = update.message.text.strip().replace("$", "").replace(",", ".")
    if text in (".", "", "д", "ок"):
        max_price = 10.0  # дефолт
    else:
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

    ctx.user_data["bo_max_price"] = max_price

    await update.message.reply_text(
        f"✅ Макс. цена: <b>${max_price:.2f}</b>\n\n"
        f"Минимальный <b>% прибыли</b> при завозе?\n"
        f"<i>Например: 5 → только предметы с маржой ≥ 5%</i>\n"
        f"<i>0 = без фильтра (по умолчанию)</i>",
        parse_mode="HTML")
    return ST_MIN_PROFIT


async def got_min_profit(update: Update,
                         ctx: ContextTypes.DEFAULT_TYPE):
    """Получили мин. прибыль → генерируем Excel."""
    text = update.message.text.strip().replace("%", "").replace(",", ".")
    if text in ("0", ".", "", "д", "ок", "нет", "-"):
        min_profit = 0.0
    else:
        try:
            min_profit = float(text)
            if min_profit < 0:
                await update.message.reply_text("❌ Введи число ≥ 0")
                return ST_MIN_PROFIT
        except ValueError:
            await update.message.reply_text("❌ Введи число (% прибыли)")
            return ST_MIN_PROFIT

    volume = ctx.user_data.get("bo_volume", 100)
    excluded = ctx.user_data.get("bo_excludes", set())
    min_price = ctx.user_data.get("bo_min_price", 0)
    max_price = ctx.user_data.get("bo_max_price", 10)

    params = {
        "volume": volume,
        "min_price": min_price,
        "max_price": max_price,
        "min_profit": min_profit,
        "excluded": list(excluded),
    }

    profit_text = f"📈 Мин. прибыль: {min_profit:.0f}%" if min_profit > 0 else ""

    # Отправляем "генерирую..."
    msg = await update.message.reply_text(
        f"⏳ <b>Генерирую БД STM-MCS...</b>\n\n"
        f"💰 Объём: ${volume:.2f}\n"
        f"📊 Цена: ${min_price:.2f} — ${max_price:.2f}\n"
        f"{profit_text}\n"
        f"🚫 Исключено: {len(excluded)} категорий",
        parse_mode="HTML")

    try:
        # Загружаем данные
        raw = _get_steamwebapi_data()
        if not raw:
            await msg.edit_text("❌ Не удалось загрузить данные SteamWebAPI")
            ctx.user_data.clear()
            return ConversationHandler.END

        items = _build_items(raw, excluded, min_price, max_price,
                             volume, min_profit=min_profit)
        if not items:
            await msg.edit_text("❌ Нет предметов по заданным параметрам")
            ctx.user_data.clear()
            return ConversationHandler.END

        filepath = _generate_excel(items, params)

        total_cost = sum(it["buy_order"] * it.get("qty", 1) for it in items)
        avg_margin = sum(it["margin"] for it in items) / len(items)

        profit_line = (f"📈 Мин. прибыль: {min_profit:.0f}%\n"
                       if min_profit > 0 else "")

        await msg.edit_text(
            f"✅ <b>БД STM-MCS готова!</b>\n\n"
            f"📦 Предметов: {len(items)} (из {len(raw)} загруженных)\n"
            f"💰 Общая стоимость: ${total_cost:.2f}\n"
            f"📈 Средняя маржа: {avg_margin:.1f}%\n"
            f"📊 Топ маржа: {items[0]['margin']:.1f}% ({items[0]['name'][:30]})\n\n"
            f"{profit_line}"
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
                        f"{len(items)} предметов"
                        f"{f' | мин.прибыль {min_profit:.0f}%' if min_profit > 0 else ''}")

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
            ST_MIN_PROFIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               got_min_profit),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(r"^📊 Создать БД STM-MCS$"),
                           start_buyorders),
            MessageHandler(
                filters.Regex(r"^(📊 Инвестиции|🔄 Круги|🌐 Прокси|⚙️ Настройки)$"),
                _cancel_and_menu),
            MessageHandler(filters.Regex(r"^/cancel$"),
                           cancel_buyorders),
        ],
        per_message=False,
        allow_reentry=True,
    )
