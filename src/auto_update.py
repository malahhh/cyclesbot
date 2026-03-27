"""Автоматическое обновление при снятии трейдбана."""

import asyncio
import json
import logging
import random
import re
import time

import db
import inventory
import pricing

log = logging.getLogger("invest")


def parse_tradeban_lifted(text: str) -> str | None:
    """Парсить сообщение Lisbot о снятии трейдбана.
    
    Ищет паттерны:
    - ✅ Трейдбан снят! [account_name]
    - ✅ Трейдбан снят у [account_name]
    - ✅ Трейдбан поднят [account_name]
    """
    if "Трейдбан" not in text or "✅" not in text:
        return None

    # Паттерны: "✅ Трейдбан снят! scrim"
    patterns = [
        r"✅\s*Трейдбан\s+(?:снят|поднят)!?\s*([a-zA-Z0-9]+)",
        r"✅\s*Трейдбан\s+(?:снят|поднят)\s+у\s*([a-zA-Z0-9]+)",
        r"✅\s*Трейдбан\s+(?:снят|поднят)\s+([a-zA-Z0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).lower()

    return None


def find_account_by_login(login: str) -> dict | None:
    """Найти аккаунт по логину в invest или circle."""
    for a in db.get_invest_accounts():
        if a["login"].lower() == login.lower():
            return a
    for a in db.get_circle_accounts():
        if a["login"].lower() == login.lower():
            return a
    return None


def _next_interval() -> float:
    """Рандомный интервал 12-24 часа для обновления."""
    return random.uniform(12 * 3600, 24 * 3600)


async def _update_steam_account_async(steam_id: str,
                                      login: str) -> bool:
    """Обновить инвентарь одного аккаунта (async).
    
    Returns True if successful.
    """
    loop = asyncio.get_event_loop()
    try:
        for app_id in (730, 570):
            # Run sync code in thread pool
            items = await loop.run_in_executor(
                None, inventory.get_inventory, steam_id, app_id)
            if not items:
                continue

            total = pricing.evaluate_inventory(items, app_id)
            items_json = json.dumps(
                [{"name": i["name"], "count": i["count"]}
                 for i in items], ensure_ascii=False)
            total_count = sum(i["count"] for i in items)

            db.save_inventory(steam_id, app_id, total_count,
                              items_json, total)
            log.info("  %s app %d: %d items, $%.2f",
                     login, app_id, total_count, total)

            await asyncio.sleep(random.uniform(2.0, 5.0))

        # Следующее обновление
        next_at = time.time() + _next_interval()
        db.set_next_update(steam_id, next_at)
        eta = (next_at - time.time()) / 3600
        log.info("  %s: след. через %.1f ч", login, eta)
        return True
    except Exception as e:
        log.error("  ❌ Ошибка обновления %s: %s", login, e)
        return False


async def handle_lisbot_message(text: str) -> bool:
    """Обработать сообщение от Lisbot.
    
    Если найдено "Трейдбан снят!" → обновить инвентарь аккаунта.
    Returns True if handled.
    """
    login = parse_tradeban_lifted(text)
    if not login:
        return False

    account = find_account_by_login(login)
    if not account:
        log.warning("Трейдбан поднят для %s — аккаунт не найден", login)
        return False

    steam_id = account["steam_id"]
    log.info("🚀 Автоматическое обновление инвентаря %s "
             "(трейдбан поднят)", login)

    result = await _update_steam_account_async(steam_id, login)
    if result:
        log.info("✅ Инвентарь %s обновлён", login)
    return result
