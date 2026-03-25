"""Investment Bot — генерация текстов для inline UI."""

import logging
from datetime import datetime, timezone, timedelta

import db

log = logging.getLogger("invest")
MSK = timezone(timedelta(hours=3))
STATUS_EMOJI = {"buy": "🟢", "hold": "🟡", "sale": "🟠", "done": "✅"}


def invest_text() -> str:
    """Текст раздела Инвестиции."""
    accs = db.get_accounts()
    if not accs:
        return "📊 <b>Инвестиции</b>\n\nНет аккаунтов."

    lines = ["📊 <b>Инвестиции</b>\n"]
    total_val = 0.0

    for acc in accs:
        inv_parts = []
        for app_id, game in [(730, "CS2"), (570, "Dota2")]:
            inv = db.get_inventory(acc["id"], app_id)
            if inv and inv["items_count"] > 0:
                inv_parts.append(
                    f"{game}: {inv['items_count']} "
                    f"(${inv['total_value']:.2f})")
                total_val += inv["total_value"]
        inv_line = " | ".join(inv_parts) if inv_parts else "—"
        lines.append(f"<b>{acc['login']}</b>\n  📦 {inv_line}")

    lines.append(f"\n💰 Итого: ${total_val:.2f}")
    return "\n".join(lines)


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
