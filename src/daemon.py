"""Фоновый демон — обновление инвентарей 1-3 раза/день."""

import json
import logging
import random
import threading
import time

import db
import inventory
import pricing
import dashboard

log = logging.getLogger("invest")

_thread = None
# Интервал: 8-16 часов (1-3 раза/день)
MIN_INTERVAL = 8 * 3600
MAX_INTERVAL = 16 * 3600


def update_account(acc: dict):
    """Обновить инвентарь одного аккаунта."""
    steam_id = acc["steam_id"]
    acc_id = acc["id"]

    for app_id in (730, 570):
        items = inventory.get_inventory(steam_id, app_id)
        if not items:
            continue
        total = pricing.evaluate_inventory(items, app_id)
        items_json = json.dumps(
            [{"name": i["name"], "count": i["count"]}
             for i in items], ensure_ascii=False)
        total_count = sum(i["count"] for i in items)

        db.save_inventory(acc_id, app_id, total_count,
                          items_json, total)
        log.info("  %s app %d: %d items, $%.2f",
                 acc["login"], app_id, total_count, total)

        # Задержка между CS2 и Dota2
        time.sleep(random.uniform(3.0, 8.0))


def run_update():
    """Один цикл: обновить все аккаунты."""
    accounts = db.get_accounts()
    if not accounts:
        return

    log.info("Обновление инвентарей: %d аккаунтов", len(accounts))
    for acc in accounts:
        try:
            update_account(acc)
        except Exception as e:
            log.error("Inventory update %s: %s", acc["login"], e)

        # Рандомная задержка между аккаунтами
        delay = random.uniform(8.0, 25.0)
        log.info("  Задержка: %.0fс", delay)
        time.sleep(delay)

    # Обновить дашборды
    try:
        dashboard.update_all()
    except Exception as e:
        log.error("Dashboard update: %s", e)

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
        # Следующее через 8-16 часов
        interval = random.uniform(MIN_INTERVAL, MAX_INTERVAL)
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
