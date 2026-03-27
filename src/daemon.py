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


def _check_day_alerts():
    """Уведомления СТРОГО на 7 и 14 дней работы круга (== не >=)."""
    from datetime import datetime
    circles = db.get_circle_accounts()
    now = datetime.now()

    for milestone in (7, 14):
        key = f"alerted_{milestone}day"
        alerted = db.get_setting(key) or ""
        alerted_set = set(alerted.split(",")) if alerted else set()

        for acc in circles:
            if acc["status"] not in ("buy", "hold", "sale"):
                continue
            login = acc["login"]
            if login in alerted_set:
                continue
            created = acc.get("created_at", "")
            if not created:
                continue
            try:
                ct = datetime.fromisoformat(str(created))
                days = (now - ct).days
                if days == milestone:
                    _send_alert(
                        f"⚠️ Аккаунт <b>{login}</b> "
                        f"в работе уже {milestone} дней!")
                    alerted_set.add(login)
                    db.set_setting(key,
                                   ",".join(alerted_set))
            except Exception:
                pass


def _send_alert(text: str):
    """Отправить уведомление в Telegram."""
    import httpx
    import config
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
            f"/sendMessage",
            json={"chat_id": config.AUTHORIZED_USER,
                  "text": text, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        log.error("Alert send error: %s", e)


def _check_proxy_expiry():
    """Алерт за 3 дня до истечения прокси."""
    from datetime import datetime
    bindings = db.get_all_proxy_bindings()
    if not bindings:
        return

    alerted = db.get_setting("proxy_expiry_alerted") or ""
    alerted_set = set(alerted.split(",")) if alerted else set()

    try:
        import httpx as _httpx
        from config import PROXYLINE_API_KEY
        headers = {"Authorization": f"Token {PROXYLINE_API_KEY}"}
        r = _httpx.get("https://panel.proxyline.net/api/proxies/",
                       headers=headers, timeout=15)
        proxies = r.json()
        if isinstance(proxies, dict):
            proxies = proxies.get("results", [])
    except Exception as e:
        log.error("Proxy expiry check: %s", e)
        return

    proxy_map = {p["id"]: p for p in proxies}

    for b in bindings:
        login = b["account_login"]
        proxy = proxy_map.get(b["proxy_id"])
        if not proxy:
            continue
        date_end = proxy.get("date_end", "")
        if not date_end:
            continue
        try:
            end = datetime.fromisoformat(
                date_end.replace("Z", "+00:00"))
            now = datetime.now(end.tzinfo)
            days_left = (end - now).days
        except Exception:
            continue

        alert_key = f"{login}_{days_left}"
        if days_left <= 3 and days_left >= 0 and alert_key not in alerted_set:
            ip = proxy.get("ip", "?")
            _send_alert(
                f"⚠️ Прокси <b>{login}</b> ({ip}) "
                f"истекает через {days_left} дн!")
            alerted_set.add(alert_key)
            db.set_setting("proxy_expiry_alerted",
                           ",".join(alerted_set))


def _loop():
    time.sleep(30)
    _init_schedules()
    _check_day_alerts()
    _check_proxy_expiry()
    _last_proxy_check = time.time()
    while True:
        try:
            run_update()
            _check_day_alerts()
            # Прокси — раз в 4 часа
            if time.time() - _last_proxy_check >= 4 * 3600:
                _check_proxy_expiry()
                _last_proxy_check = time.time()
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
