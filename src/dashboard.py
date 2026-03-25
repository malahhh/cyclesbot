"""Investment Bot — генерация текстов для inline UI."""

import logging
from datetime import datetime, timezone, timedelta

import db

log = logging.getLogger("invest")
MSK = timezone(timedelta(hours=3))
STATUS_EMOJI = {"buy": "🟢", "hold": "🟡", "sale": "🟠", "done": "✅"}


def _aggregate_inventories() -> list:
    """Собрать все предметы со всех аккаунтов из inventory_cache."""
    import json
    accs = db.get_accounts()
    merged = {}  # name -> total count

    for acc in accs:
        for app_id in (730, 570):
            inv = db.get_inventory(acc["id"], app_id)
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

    # Получаем цены из lis-sniper
    import pricing
    names = list(merged.keys())
    prices = pricing.get_price_batch(names, 730)
    # Для Dota2 тоже
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


def invest_text() -> str:
    """Текст раздела Инвестиции — реальные предметы с аккаунтов."""
    items = _aggregate_inventories()
    if not items:
        return "📊 <b>Инвестиции</b>\n\nНет предметов на аккаунтах."

    now = datetime.now(MSK).strftime("%d.%m.%Y, %H:%M МСК")
    total_qty = sum(i["qty"] for i in items)
    total_val = sum(i["total"] for i in items)

    # Все предметы, сортировка по стоимости
    all_items = sorted(items, key=lambda x: x["total"],
                       reverse=True)

    # Полные названия, горизонтальный скролл в <pre>
    W = 40  # ширина колонки Name
    rows = []
    for item in all_items:
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
        f"📦 Предметов: {total_qty}\n"
        f"💰 Оценка: ${total_val:.2f}\n"
        f"🕐 {now}\n")

    hdr = f"{'Предмет':<{W}}│ Кол│  Цена │  Всего"
    sep = ("─" * W + "┼" + "─" * 4 + "┼" +
           "─" * 7 + "┼" + "─" * 8)
    table = "\n".join([hdr, sep] + rows)

    priced = len([i for i in items if i["price"] > 0])
    footer = f"\nОценено: {priced}/{len(items)} предметов"

    full = f"{header}\n<pre>{table}{footer}</pre>"

    # Telegram лимит 4096 — разбиваем если нужно
    if len(full) <= 4096:
        return full

    # Показываем топ по стоимости, сколько влезет
    parts = [header, "<pre>" + hdr + "\n" + sep]
    cur_len = len(header) + len(hdr) + len(sep) + 15  # pre tags
    shown = 0
    for row in rows:
        if cur_len + len(row) + 2 > 3900:  # запас
            break
        parts.append(row)
        cur_len += len(row) + 1
        shown += 1

    remaining = len(all_items) - shown
    parts.append(
        f"{footer}\n"
        f"Показано: {shown}/{len(all_items)}"
        f" (ещё {remaining})</pre>")
    return "\n".join(parts)


def circles_text() -> str:
    """Текст раздела Круги (активные)."""
    accs = db.get_accounts()
    active = [a for a in accs
              if a["status"] in ("buy", "hold", "sale")]
    if not active:
        return "🔄 <b>Круги</b>\n\nНет активных кругов."

    lines = ["🔄 <b>Круги</b>\n"]
    total_amount = 0.0

    for acc in active:
        emoji = STATUS_EMOJI.get(acc["status"], "⚪")
        inv_parts = []
        for app_id, game in [(730, "CS2"), (570, "Dota2")]:
            inv = db.get_inventory(acc["id"], app_id)
            if inv and inv["items_count"] > 0:
                inv_parts.append(
                    f"{game}: {inv['items_count']} "
                    f"(${inv['total_value']:.2f})")
        inv_line = " | ".join(inv_parts) if inv_parts else "—"

        try:
            amt = float(acc["amount"].replace("$", "")
                        .split("+")[0].strip())
            total_amount += amt
        except (ValueError, IndexError):
            pass

        block = [f"<b>{acc['login']}</b> | {acc['amount']}"]
        block.append(f"  📦 {inv_line}")
        if acc["scheme"]:
            block.append(f"  🔄 {acc['scheme']}")
        if acc["check_note"]:
            block.append(f"  📋 {acc['check_note']}")
        block.append(f"  {emoji} {acc['status']}")
        lines.append("\n".join(block))

    lines.append(f"\n💰 Вложено: ${total_amount:.0f}")
    return "\n\n".join(lines)


def history_text() -> str:
    """Текст раздела История (завершённые круги)."""
    accs = db.get_accounts()
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
