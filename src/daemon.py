"""Фоновый демон — обновление инвентарей раз в сутки."""

import json
import logging
import random
import threading
import time

import db
import inventory
import pricing

log = logging.getLogger("invest")

_thread = None
INTERVAL = 24 * 3600  # раз в сутки


def update_steam_account(steam_id: str, login: str):
    """Обновить инвентарь одного аккаунта по steam_id."""
    for app_id in (730, 570):
        items = inventory.get_inventory(steam_id, app_id)
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

        time.sleep(random.uniform(3.0, 8.0))


def run_update():
    """Один цикл: обновить invest + circle accounts."""
    inv_accounts = db.get_invest_accounts()
    cir_accounts = db.get_circle_accounts()
    active_cir = [a for a in cir_accounts
                  if a["status"] in ("buy", "hold", "sale")]

    # Уникальные steam_id
    seen = set()
    to_update = []
    for acc in inv_accounts + active_cir:
        if acc["steam_id"] not in seen:
            seen.add(acc["steam_id"])
            to_update.append(acc)

    if not to_update:
        log.info("Нет аккаунтов для обновления")
        return

    log.info("Обновление инвентарей: %d аккаунтов", len(to_update))
    for acc in to_update:
        try:
            update_steam_account(acc["steam_id"], acc["login"])
        except Exception as e:
            log.error("Inventory update %s: %s", acc["login"], e)

        delay = random.uniform(8.0, 25.0)
        log.info("  Задержка: %.0fс", delay)
        time.sleep(delay)

    log.info("Обновление завершено")


def _loop():
    """Фоновый цикл."""
    # Первое обновление через 30с после старта
    time.sleep(30)
    while True:
        try:
            run_update()
        except Exception as e:
            log.error("Daemon error: %s", e)
        # Следующее через 24ч (± 1ч рандом)
        interval = INTERVAL + random.uniform(-3600, 3600)
        log.info("Следующее обновление через %.1f ч",
                 interval / 3600)
        time.sleep(interval)


def start():
    """Запуск демона."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True,
                               name="invest-daemon")
    _thread.start()
    log.info("Investment daemon started")
