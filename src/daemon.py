"""Фоновый демон — индивидуальные таймеры обновления (12-24ч)."""

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

MIN_INTERVAL = 12 * 3600
MAX_INTERVAL = 24 * 3600


def _next_interval() -> float:
    return random.uniform(MIN_INTERVAL, MAX_INTERVAL)


def _get_all_unique_accounts() -> list:
    """Все уникальные (steam_id, login)."""
    seen = set()
    result = []
    for acc in db.get_invest_accounts():
        if acc["steam_id"] not in seen:
            seen.add(acc["steam_id"])
            result.append((acc["steam_id"], acc["login"]))
    for acc in db.get_circle_accounts():
        if (acc["steam_id"] not in seen
                and acc["status"] in ("buy", "hold", "sale")):
            seen.add(acc["steam_id"])
            result.append((acc["steam_id"], acc["login"]))
    return result


def _init_schedules():
    """Инициализация расписания для новых аккаунтов."""
    all_accounts = _get_all_unique_accounts()
    now = time.time()

    for i, (steam_id, login) in enumerate(all_accounts):
        existing = db.get_next_update(steam_id)
        if existing > 0:
            continue

        # Проверяем кэш
        latest = 0
        for app_id in (730, 570):
            inv = db.get_inventory(steam_id, app_id)
            if inv and inv.get("updated_at", 0) > latest:
                latest = inv["updated_at"]

        if latest > 0:
            next_at = latest + _next_interval()
            if next_at < now:
                next_at = now + random.uniform(
                    60 + i * 1800, 60 + i * 1800 + 3600)
        else:
            next_at = now + random.uniform(
                60 + i * 3600, 14400 + i * 3600)

        db.set_next_update(steam_id, next_at)
        eta = (next_at - now) / 3600
        log.info("  Schedule %s (%s): через %.1f ч",
                 login, steam_id[:10], eta)


def update_steam_account(steam_id: str, login: str):
    """Обновить инвентарь одного аккаунта."""
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

    # Следующее обновление
    next_at = time.time() + _next_interval()
    db.set_next_update(steam_id, next_at)
    eta = (next_at - time.time()) / 3600
    log.info("  %s: след. через %.1f ч", login, eta)


def run_update():
    """Проверить и обновить аккаунты по расписанию."""
    _init_schedules()
    all_accounts = _get_all_unique_accounts()
    now = time.time()
    updated = 0

    for steam_id, login in all_accounts:
        next_at = db.get_next_update(steam_id)
        if next_at > now:
            continue

        log.info("Обновляю %s...", login)
        try:
            update_steam_account(steam_id, login)
            updated += 1
        except Exception as e:
            log.error("Ошибка %s: %s", login, e)
            db.set_next_update(steam_id,
                               now + random.uniform(3600, 7200))

        delay = random.uniform(8.0, 25.0)
        log.info("  Задержка: %.0fс", delay)
        time.sleep(delay)

    if updated:
        log.info("Обновлено: %d аккаунтов", updated)


def _loop():
    time.sleep(30)
    _init_schedules()
    while True:
        try:
            run_update()
        except Exception as e:
            log.error("Daemon error: %s", e)
        time.sleep(600)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True,
                               name="invest-daemon")
    _thread.start()
    log.info("Investment daemon started")
