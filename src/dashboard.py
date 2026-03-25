"""Investment Bot — генерация текстов для inline UI."""

import logging
from datetime import datetime, timezone, timedelta

import db

log = logging.getLogger("invest")
MSK = timezone(timedelta(hours=3))
STATUS_EMOJI = {"buy": "🟢", "hold": "🟡", "sale": "🟠", "done": "✅"}


def invest_text() -> str:
    """Текст раздела Инвестиции (CS2 таблица)."""
    items = db.get_cs2_investments()
    if not items:
        return "📈 <b>Дашборд инвестиций CS2</b>\n\nНет данных."

    now = datetime.now(MSK).strftime("%d.%m.%Y, %H:%M МСК")
    total_qty = 0
    total_buy = 0.0
    total_steam = 0.0
    total_mc = 0.0

    rows = []
    for item in items:
        qty = item["qty"]
        bp = item["buy_price"]
        sp = item["steam_price"]
        mc = item["market_csgo_price"]
        ps = item.get("prev_steam") or sp
        pm = item.get("prev_mc") or mc

        # Δ%
        ds = ((sp - ps) / ps * 100) if ps > 0 else 0
        dm = ((mc - pm) / pm * 100) if pm > 0 else 0
        ds_icon = "🟢" if ds > 10 else ("🔴" if ds < -5 else "")
        dm_icon = "🟢" if dm > 10 else ("🔴" if dm < -5 else "")

        # Сокращённое имя
        short = (item["name"].replace(" Case", "")
                 .replace("Desert Eagle | Tilted (Factory New)",
                          "Deagle FN"))
        if len(short) > 12:
            short = short[:11] + "…"

        q_s = f"{qty:>3}" if qty > 0 else "  —"
        b_s = f"{bp:>4.2f}" if bp > 0 else "   —"
        rows.append(
            f"{short:<12}│{q_s}│{bp:>5.2f}│{sp:>5.2f}│"
            f"{ds:>+5.1f}%{ds_icon}│{mc:>5.2f}│"
            f"{dm:>+5.1f}%{dm_icon}")

        total_qty += qty
        total_buy += bp * qty
        total_steam += sp * qty
        total_mc += mc * qty

    net_steam = total_steam * 0.87
    pnl = net_steam - total_buy
    pnl_pct = (pnl / total_buy * 100) if total_buy > 0 else 0
    pnl_emoji = "📈" if pnl >= 0 else "📉"

    header = (
        f"📈 <b>Дашборд инвестиций CS2</b>\n"
        f"💰 Вложено: ${total_buy:.2f} ({total_qty} шт)\n"
        f"💵 Сейчас: ${total_steam:.2f} / "
        f"${total_mc:.2f}\n"
        f"{pnl_emoji} PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"🕐 {now}\n")

    hdr = (f"{'':12}│Qty│  Avg│  Stm│   ΔS│   TM│   ΔM")
    sep = "─" * 12 + "┼" + "─" * 3 + "┼" + "─" * 5 + "┼" + \
          "─" * 5 + "┼" + "─" * 7 + "┼" + "─" * 5 + "┼" + "─" * 7
    table = "\n".join([hdr, sep] + rows)

    return f"{header}\n<pre>{table}</pre>"


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
