"""Миграция из circles_dashboard.json + cs2_investment_prices.json."""

import json
import logging
import db

log = logging.getLogger("invest")

CIRCLES_JSON = ("/home/openclawd/.openclaw/workspace/"
                "circles_dashboard.json")
CS2_JSON = ("/home/openclawd/.openclaw/workspace/"
            "cs2_investment_prices.json")


def migrate_accounts():
    """Импорт аккаунтов из circles_dashboard.json."""
    try:
        with open(CIRCLES_JSON) as f:
            data = json.load(f)
    except FileNotFoundError:
        log.warning("circles_dashboard.json не найден")
        return 0

    count = 0
    for acc in data.get("accounts", []):
        login = acc.get("login", "")
        steam_id = acc.get("steamId", "")
        if not login or not steam_id:
            continue
        existing = db.get_account_by_login(login)
        if existing:
            continue
        db.add_account(
            login, steam_id,
            amount=acc.get("amount", ""),
            scheme=acc.get("scheme", ""),
            status=acc.get("status", "buy"),
            checked_at=acc.get("checkedAt", ""),
            check_note=acc.get("checkNote", ""))
        count += 1
        log.info("Мигрирован: %s (%s)", login, steam_id[:15])
    log.info("Миграция аккаунтов: %d добавлено", count)
    return count


def migrate_cs2():
    """Импорт CS2 инвестиций из cs2_investment_prices.json."""
    try:
        with open(CS2_JSON) as f:
            data = json.load(f)
    except FileNotFoundError:
        log.warning("cs2_investment_prices.json не найден")
        return 0

    count = 0
    for name, prices in data.get("items", {}).items():
        qty = prices.get("qty", 0)
        buy_price = prices.get("buy_price", 0)
        steam = prices.get("steam", 0)
        mc = prices.get("market_csgo", 0)
        db.save_cs2_investment(name, qty, buy_price, steam, mc)
        count += 1
    log.info("Миграция CS2: %d предметов", count)
    return count


def run():
    """Полная миграция."""
    a = migrate_accounts()
    c = migrate_cs2()
    return a, c


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    sys.path.insert(0, ".")
    run()
