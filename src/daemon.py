"""Фоновый демон — индивидуальные таймеры обновления для каждого аккаунта."""

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

# Интервал обновления: 12-24 часа (рандом для каждого аккаунта)
MIN_INTERVAL = 12 * 3600
MAX_INTERVAL = 24 * 3600

# {steam_id: next_update_timestamp}
_schedules: dict[str, float] = {}


def _next_interval() -> float:
    """Рандомный интервал 18-30 часов."""
    return random.uniform(MIN_INTERVAL, MAX_INTERVAL)


def _init_schedules():
    """Инициализация расписания: рандомный offset для каждого аккаунта."""
    all_accounts = _get_all_unique_accounts()
    now = time.time()
    offset_min = 60      # минимум 1 минута
    offset_max = 14400   # максимум 4 часа

    for i, (steam_id, login) in enumerate(all_accounts):
        if steam_id in _schedules:
            continue
        # Проверяем когда последний раз обновлялся
        latest = 0
        for app_id in (730, 570):
            inv = db.get_inventory(steam_id, app_id)
            if inv and inv.get("updated_at", 0) > latest:
                latest = inv["updated_at"]

        if latest > 0:
            # Есть кэш — следующее обновление через 18-30ч от последнего
            next_at = latest + _next_interval()
            if next_at < now:
                # Просрочен — обновить скоро, но с рандомным offset
                next_at = now + random.uniform(
                    offset_min + i * 1800,
                    offset_min + i * 1800 + 3600)
        else:
            # Нет кэша — обновить с рандомным offset
            # Каждый следующий аккаунт позже предыдущего
            next_at = now + random.uniform(
                offset_min + i * 3600,
                offset_max + i * 3600)

        _schedules[steam_id] = next_at
        eta = (next_at - now) / 3600
        log.info("  Schedule %s (%s): через %.1f ч", login,
                 steam_id[:10], eta)


def _get_all_unique_accounts() -> list:
    """Все уникальные (steam_id, login) из invest + circles."""
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

    # Запланировать следующее обновление
    _schedules[steam_id] = time.time() + _next_interval()
    eta = (_schedules[steam_id] - time.time()) / 3600
    log.info("  %s: след. обновление через %.1f ч", login, eta)


def get_next_update(steam_id: str) -> float:
    """Время следующего обновления для аккаунта (timestamp)."""
    return _schedules.get(steam_id, 0)


def run_update():
    """Проверить и обновить аккаунты, у которых пришло время."""
    _init_schedules()
    all_accounts = _get_all_unique_accounts()
    now = time.time()
    updated = 0

    for steam_id, login in all_accounts:
        next_at = _schedules.get(steam_id, 0)
        if next_at > now:
            continue

        log.info("Обновляю %s...", login)
        try:
            update_steam_account(steam_id, login)
            updated += 1
        except Exception as e:
            log.error("Ошибка %s: %s", login, e)
            # Retry через 1-2 часа
            _schedules[steam_id] = now + random.uniform(3600, 7200)

        # Задержка между аккаунтами
        delay = random.uniform(8.0, 25.0)
        log.info("  Задержка: %.0fс", delay)
        time.sleep(delay)

    if updated:
        log.info("Обновлено: %d аккаунтов", updated)


def _loop():
    """Фоновый цикл — проверка каждые 10 минут."""
    time.sleep(30)  # старт через 30с
    _init_schedules()

    while True:
        try:
            run_update()
        except Exception as e:
            log.error("Daemon error: %s", e)

        # Проверяем каждые 10 минут
        time.sleep(600)


def start():
    """Запуск демона."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True,
                               name="invest-daemon")
    _thread.start()
    log.info("Investment daemon started")
