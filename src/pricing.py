"""Оценка инвентаря через lis-sniper БД (read-only)."""

import logging
import sqlite3
from typing import Optional

from config import SNIPER_DB_PATH

log = logging.getLogger("invest")

_conn: Optional[sqlite3.Connection] = None


def _get_sniper_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(SNIPER_DB_PATH,
                                check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def get_price(name: str, app_id: int = 730) -> float:
    """Цена предмета из zakup_items (steam_price)."""
    try:
        r = _get_sniper_db().execute(
            """SELECT steam_price FROM zakup_items
               WHERE name=? AND app_id=?
               AND steam_price > 0
               LIMIT 1""",
            (name, app_id)).fetchone()
        return float(r[0]) if r else 0.0
    except Exception:
        return 0.0


def evaluate_inventory(items: list, app_id: int = 730) -> float:
    """Оценить инвентарь. items = [{name, count}]."""
    total = 0.0
    for item in items:
        price = get_price(item["name"], app_id)
        total += price * item["count"]
    return round(total, 2)


def get_price_batch(names: list, app_id: int = 730) -> dict:
    """Цены для списка предметов. {name: price}."""
    if not names:
        return {}
    try:
        placeholders = ",".join("?" * len(names))
        rows = _get_sniper_db().execute(
            f"""SELECT name, steam_price FROM zakup_items
                WHERE name IN ({placeholders})
                AND app_id=? AND steam_price > 0""",
            names + [app_id]).fetchall()
        return {r[0]: float(r[1]) for r in rows}
    except Exception:
        return {}
