"""Генерация и обновление дашбордов в Telegram канале."""

import json
import logging
from datetime import datetime, timezone, timedelta

import httpx

import db
from config import TELEGRAM_TOKEN, CHANNEL_ID, CIRCLES_MSG_ID, CS2_MSG_ID

log = logging.getLogger("invest")
MSK = timezone(timedelta(hours=3))

STATUS_EMOJI = {
    "buy": "🟢", "hold": "🟡", "sale": "🟠", "done": "✅",
}


def _edit_message(msg_id: int, text: str):
    """Обновить сообщение в канале через Bot API."""
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
            f"/editMessageText",
            json={
                "chat_id": CHANNEL_ID,
                "message_id": msg_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10)
        data = r.json()
        if not data.get("ok"):
            log.error("Edit msg %d: %s", msg_id,
                      data.get("description", "?"))
    except Exception as e:
        log.error("Edit msg %d: %s", msg_id, e)


def generate_circles_text() -> str:
    """Генерация текста дашборда кругов."""
    accounts = db.get_accounts()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M МСК")

    lines = [f"📊 <b>Круги инвестиций</b>",
             f"обновлено: {now}\n"]

    total_amount = 0.0
    total_inv = 0.0

    for acc in accounts:
        emoji = STATUS_EMOJI.get(acc["status"], "⚪")
        inv_parts = []
        for app_id, game in [(730, "CS2"), (570, "Dota2")]:
            inv = db.get_inventory(acc["id"], app_id)
            if inv and inv["items_count"] > 0:
                inv_parts.append(
                    f"{game}: {inv['items_count']} шт "
                    f"(${inv['total_value']:.2f})")
                total_inv += inv["total_value"]

        inv_line = " | ".join(inv_parts) if inv_parts else "нет данных"

        # Парсим amount
        try:
            amt_str = acc["amount"].replace("$", "").split("+")[0]
            total_amount += float(amt_str.strip())
        except (ValueError, IndexError):
            pass

        block = [
            f"│ <b>{acc['login']}</b> | {acc['amount']}",
            f"│ 📦 {inv_line}",
        ]
        if acc["scheme"]:
            block.append(f"│ 🔄 {acc['scheme']}")
        if acc["check_note"]:
            block.append(f"│ 📋 {acc['check_note']}")
        if acc["checked_at"]:
            block.append(f"│ ⏱ проверен: {acc['checked_at']}")
        block.append(f"│ {emoji} {acc['status']}")

        lines.append("┌" + "─" * 30)
        lines.extend(block)

    lines.append("└" + "─" * 30)
    lines.append(f"\n💰 Итого: ${total_amount:.0f} вложено"
                 f" | ${total_inv:.2f} в инвентарях")
    return "\n".join(lines)


def generate_cs2_text() -> str:
    """Генерация текста дашборда CS2 инвестиций."""
    items = db.get_cs2_investments()
    if not items:
        return "📈 CS2 инвестиции: нет данных"

    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M МСК")
    lines = [f"📈 <b>CS2 Инвестиции</b>",
             f"обновлено: {now}\n"]

    total_buy = 0.0
    total_steam = 0.0
    total_mc = 0.0

    for item in items:
        qty = item["qty"]
        bp = item["buy_price"]
        sp = item["steam_price"]
        mc = item["market_csgo_price"]
        buy_total = bp * qty
        steam_total = sp * qty
        mc_total = mc * qty

        # Маржа Steam
        if bp > 0:
            margin = ((sp * 0.87 - bp) / bp) * 100
        else:
            margin = 0

        m_emoji = "📈" if margin > 0 else "📉"
        lines.append(
            f"<b>{item['name']}</b> x{qty}\n"
            f"  Buy: ${bp:.2f} | Steam: ${sp:.2f} | "
            f"MC: ${mc:.2f}\n"
            f"  {m_emoji} Маржа: {margin:+.1f}% "
            f"(${steam_total * 0.87 - buy_total:.2f})")

        total_buy += buy_total
        total_steam += steam_total
        total_mc += mc_total

    total_margin = 0
    if total_buy > 0:
        total_margin = ((total_steam * 0.87 - total_buy)
                        / total_buy) * 100

    lines.append(f"\n💰 Вложено: ${total_buy:.2f}")
    lines.append(f"🎮 Steam: ${total_steam:.2f} "
                 f"(нетто ${total_steam * 0.87:.2f})")
    lines.append(f"🏪 MarketCSGO: ${total_mc:.2f}")
    lines.append(f"📊 Маржа: {total_margin:+.1f}%")
    return "\n".join(lines)


def update_circles():
    """Обновить дашборд кругов в канале."""
    text = generate_circles_text()
    _edit_message(CIRCLES_MSG_ID, text)
    log.info("Circles dashboard updated")


def update_cs2():
    """Обновить дашборд CS2 в канале."""
    text = generate_cs2_text()
    _edit_message(CS2_MSG_ID, text)
    log.info("CS2 dashboard updated")


def update_all():
    """Обновить оба дашборда."""
    update_circles()
    update_cs2()
