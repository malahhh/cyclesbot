"""Investment Bot — генерация текстов для inline UI."""

import logging
from datetime import datetime, timezone, timedelta

import db

log = logging.getLogger("invest")
MSK = timezone(timedelta(hours=3))
STATUS_EMOJI = {"buy": "🟢", "hold": "🟡", "sale": "🟠", "done": "✅"}


def _aggregate_invest_inventories() -> list:
    """Собрать предметы с invest_accounts из inventory_cache."""
    import json
    accs = db.get_invest_accounts()
    merged = {}  # name -> total count

    for acc in accs:
        for app_id in (730, 570):
            inv = db.get_inventory(acc["steam_id"], app_id)
            if not inv or not inv.get("items_json"):
                continue
            try:
                items = json.loads(inv["items_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            for item in items:
                name = item.get("name", "")
                count = item.get("count", 0)
                if name and count > 0:
                    merged[name] = merged.get(name, 0) + count

    import pricing
    names = list(merged.keys())
    prices = pricing.get_price_batch(names, 730)
    prices_dota = pricing.get_price_batch(names, 570)
    for n, p in prices_dota.items():
        if n not in prices:
            prices[n] = p

    result = []
    for name, qty in sorted(merged.items(),
                             key=lambda x: x[1], reverse=True):
        price = prices.get(name, 0)
        result.append({"name": name, "qty": qty,
                        "price": price,
                        "total": price * qty})
    return result


INVEST_PAGE_SIZE = 30  # строк на страницу


def invest_text(page: int = 0) -> str:
    """Текст раздела Инвестиции — страница page."""
    items = _aggregate_invest_inventories()
    if not items:
        return "📊 <b>Инвестиции</b>\n\nНет предметов на аккаунтах."

    import json as _json
    now = datetime.now(MSK).strftime("%d.%m.%Y, %H:%M МСК")
    total_qty = sum(i["qty"] for i in items)
    total_val = sum(i["total"] for i in items)
    priced = len([i for i in items if i["price"] > 0])

    # Список аккаунтов с инфой
    accs = db.get_invest_accounts()
    acc_lines = []
    for acc in accs:
        parts = []
        latest_upd = 0
        for app_id, game in [(730, "CS2"), (570, "Dota2")]:
            inv = db.get_inventory(acc["steam_id"], app_id)
            if inv and inv["items_count"] > 0:
                parts.append(
                    f"{game}: {inv['items_count']} шт / "
                    f"${inv['total_value']:.2f}")
                if inv["updated_at"] and inv["updated_at"] > latest_upd:
                    latest_upd = inv["updated_at"]
        if parts:
            line = f"• {acc['login']} — {' | '.join(parts)}"
            if latest_upd:
                upd_dt = datetime.fromtimestamp(latest_upd, MSK)
                next_ts = db.get_next_update(acc["steam_id"])
                line += f"\n  обновлено {upd_dt.strftime('%d.%m %H:%M')}"
                if next_ts:
                    import time as _time
                    eta_h = (next_ts - _time.time()) / 3600
                    if eta_h > 0:
                        next_dt = datetime.fromtimestamp(next_ts, MSK)
                        line += (f" | след. через {eta_h:.0f}ч "
                                 f"(~{next_dt.strftime('%H:%M')} МСК)")
                    else:
                        line += " | обновление скоро"
            acc_lines.append(line)
        else:
            acc_lines.append(f"• {acc['login']} — нет данных")
    acc_block = "\n".join(acc_lines)

    all_items = sorted(items, key=lambda x: x["total"],
                       reverse=True)

    total_pages = max(1, -(-len(all_items) // INVEST_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * INVEST_PAGE_SIZE
    end = start + INVEST_PAGE_SIZE
    page_items = all_items[start:end]

    W = 40
    rows = []
    for item in page_items:
        name = item["name"]
        if len(name) > W:
            name = name[:W - 1] + "…"
        p = item["price"]
        t = item["total"]
        p_s = f"${p:.2f}" if p > 0 else "—"
        t_s = f"${t:.2f}" if t > 0 else "—"
        rows.append(
            f"{name:<{W}}│{item['qty']:>4}│"
            f"{p_s:>7}│{t_s:>8}")

    header = (
        f"📊 <b>Инвестиции</b>\n"
        f"📦 {len(all_items)} уникальных / {total_qty} шт\n"
        f"💰 Оценка: ${total_val:.2f}\n\n"
        f"📋 Аккаунты:\n{acc_block}\n\n"
        f"🕐 {now}\n")

    hdr = f"{'Предмет':<{W}}│ Кол│  Цена │  Всего"
    sep = ("─" * W + "┼" + "─" * 4 + "┼" +
           "─" * 7 + "┼" + "─" * 8)
    table = "\n".join([hdr, sep] + rows)
    footer = (f"\nОценено: {priced}/{len(items)} | "
              f"Стр {page + 1}/{total_pages}")

    return f"{header}\n<pre>{table}{footer}</pre>"


def invest_pages() -> int:
    """Количество страниц инвестиций."""
    items = _aggregate_invest_inventories()
    return max(1, -(-len(items) // INVEST_PAGE_SIZE))


def circles_text() -> str:
    """Текст раздела Круги (активные)."""
    accs = db.get_circle_accounts()
    active = [a for a in accs
              if a["status"] in ("buy", "hold", "sale")]
    if not active:
        return "🔄 <b>Круги</b>\n\nНет активных кругов."

    SEP = "━" * 22
    blocks = ["🔄 <b>Круги</b>\n"]
    total_amount = 0.0

    for acc in active:
        emoji = STATUS_EMOJI.get(acc["status"], "⚪")

        try:
            amt = float(acc["amount"].replace("$", "")
                        .split("+")[0].strip())
            total_amount += amt
        except (ValueError, IndexError):
            pass

        cs2_count, cs2_value = 0, 0.0
        dota_count, dota_value = 0, 0.0
        inv_cs2 = db.get_inventory(acc["steam_id"], 730)
        if inv_cs2 and inv_cs2["items_count"] > 0:
            cs2_count = inv_cs2["items_count"]
            cs2_value = inv_cs2["total_value"]
        inv_dota = db.get_inventory(acc["steam_id"], 570)
        if inv_dota and inv_dota["items_count"] > 0:
            dota_count = inv_dota["items_count"]
            dota_value = inv_dota["total_value"]
        total_count = cs2_count + dota_count
        total_value = cs2_value + dota_value
        
        inv_lines = []
        if cs2_count > 0:
            inv_lines.append(f"📦 CS2: {cs2_count} шт | 💵 ${cs2_value:.2f}")
        if dota_count > 0:
            inv_lines.append(f"📦 Dota2: {dota_count} шт | 💵 ${dota_value:.2f}")
        if not inv_lines:
            inv_lines.append("📦 Предметов: ??")
        inv_text = "\n".join(inv_lines)
        total_s = f"💵 Общая оценка: ${total_value:.2f}" if total_value > 0 else "💵 Общая оценка: ??"

        # След. обновление
        import time as _time
        next_ts = db.get_next_update(acc["steam_id"])
        if next_ts:
            eta_h = (next_ts - _time.time()) / 3600
            next_dt = datetime.fromtimestamp(next_ts, MSK)
            if eta_h > 0:
                next_s = (f"через {eta_h:.0f}ч "
                          f"(~{next_dt.strftime('%H:%M')} МСК)")
            else:
                next_s = "скоро"
        else:
            next_s = "??"

        # Дата создания круга + длительность
        created = acc.get('created_at', '')
        if created:
            try:
                from datetime import datetime as _dt
                ct = _dt.fromisoformat(str(created).replace('Z', '+00:00'))
                created_s = ct.strftime('%d.%m.%Y')
                days = (datetime.now() - ct.replace(tzinfo=None)).days
                duration_s = f" ({days} дн.)"
            except Exception:
                created_s = str(created)[:10]
                duration_s = ""
        else:
            created_s = '??'
            duration_s = ""

        card = (
            f"🟦 Круг #{acc['id']} — Аккаунт: {acc['login']}\n"
            f"📅 Создан: {created_s}{duration_s}\n"
            f"💰 Вложено: {acc['amount'] or '??'}\n"
            f"{inv_text}\n"
            f"{total_s}\n"
            f"🔁 Схема: {acc['scheme'] or '??'}\n"
            f"⚠️ Статус схемы: {acc['check_note'] or '??'}\n"
            f"📝 Примечание: {acc['status'] or '??'}\n"
            f"🕐 След. обновление: {next_s}\n"
            f"\n{SEP}\n\n")
        blocks.append(card)

    blocks.append(f"\n💰 Вложено: ${total_amount:.0f}")
    return "\n".join(blocks)


def history_text() -> str:
    """Текст раздела История (завершённые круги)."""
    accs = db.get_circle_accounts(include_done=True)
    done = [a for a in accs if a["status"] == "done"]
    if not done:
        return "📜 <b>История</b>\n\nНет завершённых кругов."

    lines = ["📜 <b>История</b>\n"]
    total_invested = 0.0
    total_withdrawn = 0.0

    for acc in done:
        # Парсим check_note: "Вывод: $X, P/L: $Y (Z%)"
        note = acc.get("check_note", "")
        try:
            invested = float(acc["amount"].replace("$", "")
                             .split("+")[0].strip())
        except (ValueError, IndexError):
            invested = 0

        total_invested += invested
        # Пытаемся достать вывод из заметки
        withdrawn = 0
        if "Вывод:" in note:
            try:
                w_str = note.split("Вывод:")[1].split(",")[0]
                withdrawn = float(w_str.replace("$", "").strip())
            except (ValueError, IndexError):
                pass
        total_withdrawn += withdrawn
        profit = withdrawn - invested
        emoji = "📈" if profit >= 0 else "📉"

        lines.append(
            f"✅ <b>{acc['login']}</b> | {acc['amount']}\n"
            f"  🔄 {acc['scheme']}\n"
            f"  {emoji} {note}")

    total_pnl = total_withdrawn - total_invested
    total_roi = ((total_pnl / total_invested * 100)
                 if total_invested > 0 else 0)
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"

    lines.append(
        f"\n{pnl_emoji} <b>Итого:</b>\n"
        f"  Вложено: ${total_invested:.2f}\n"
        f"  Выведено: ${total_withdrawn:.2f}\n"
        f"  P/L: ${total_pnl:+.2f} ({total_roi:+.1f}%)")
    return "\n\n".join(lines)
