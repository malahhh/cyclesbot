"""Investment Bot — SQLite БД.

Две раздельные таблицы:
- invest_accounts — долгосрочное хранение (раздел Инвестиции)
- circle_accounts — быстрый оборот (раздел Круги)
"""

import sqlite3
import time
from typing import Optional

from config import DB_PATH

_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_tables()
    return _conn


def _init_tables():
    c = get_conn()
    c.executescript("""
        -- Инвестиции: долгосрочное хранение
        CREATE TABLE IF NOT EXISTS invest_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            steam_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Круги: быстрый оборот
        CREATE TABLE IF NOT EXISTS circle_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT NOT NULL,
            steam_id TEXT NOT NULL,
            amount TEXT DEFAULT '',
            scheme TEXT DEFAULT '',
            status TEXT DEFAULT 'buy',
            check_note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Кэш инвентарей (общий, ключ = steam_id + app_id)
        CREATE TABLE IF NOT EXISTS inventory_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_id TEXT NOT NULL,
            app_id INTEGER NOT NULL,
            items_count INTEGER DEFAULT 0,
            items_json TEXT DEFAULT '[]',
            total_value REAL DEFAULT 0,
            updated_at REAL DEFAULT 0,
            UNIQUE(steam_id, app_id)
        );

        -- Расписание обновлений (ключ = steam_id)
        CREATE TABLE IF NOT EXISTS update_schedule (
            steam_id TEXT PRIMARY KEY,
            next_update_at REAL DEFAULT 0
        );
    """)
    c.commit()


# ============================================================
# Invest accounts (Инвестиции)
# ============================================================
def get_invest_accounts() -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM invest_accounts ORDER BY id").fetchall()]


def get_invest_account(aid: int) -> dict:
    r = get_conn().execute(
        "SELECT * FROM invest_accounts WHERE id=?", (aid,)
    ).fetchone()
    return dict(r) if r else {}


def add_invest_account(login: str, steam_id: str) -> int:
    c = get_conn()
    c.execute("INSERT INTO invest_accounts (login, steam_id) "
              "VALUES (?, ?)", (login, steam_id))
    c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def delete_invest_account(aid: int) -> bool:
    c = get_conn()
    acc = get_invest_account(aid)
    if acc:
        c.execute("DELETE FROM inventory_cache WHERE steam_id=?",
                  (acc["steam_id"],))
    c.execute("DELETE FROM invest_accounts WHERE id=?", (aid,))
    c.commit()
    return c.total_changes > 0


# ============================================================
# Circle accounts (Круги)
# ============================================================
def get_circle_accounts() -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM circle_accounts ORDER BY id").fetchall()]


def get_circle_account(aid: int) -> dict:
    r = get_conn().execute(
        "SELECT * FROM circle_accounts WHERE id=?", (aid,)
    ).fetchone()
    return dict(r) if r else {}


def add_circle_account(login: str, steam_id: str, **kw) -> int:
    c = get_conn()
    c.execute(
        """INSERT INTO circle_accounts
           (login, steam_id, amount, scheme, status, check_note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (login, steam_id, kw.get("amount", ""),
         kw.get("scheme", ""), kw.get("status", "buy"),
         kw.get("check_note", "")))
    c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_circle_account(aid: int, **fields) -> bool:
    allowed = {"login", "steam_id", "amount", "scheme",
               "status", "check_note"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    vals.append(aid)
    c = get_conn()
    c.execute(
        f"UPDATE circle_accounts SET {','.join(sets)} WHERE id=?",
        vals)
    c.commit()
    return c.total_changes > 0


def delete_circle_account(aid: int) -> bool:
    c = get_conn()
    c.execute("DELETE FROM circle_accounts WHERE id=?", (aid,))
    c.commit()
    return c.total_changes > 0


# ============================================================
# Inventory cache (общий по steam_id)
# ============================================================
def save_inventory(steam_id: str, app_id: int,
                   items_count: int, items_json: str,
                   total_value: float):
    c = get_conn()
    c.execute(
        """INSERT INTO inventory_cache
           (steam_id, app_id, items_count, items_json,
            total_value, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(steam_id, app_id) DO UPDATE SET
           items_count=excluded.items_count,
           items_json=excluded.items_json,
           total_value=excluded.total_value,
           updated_at=excluded.updated_at""",
        (steam_id, app_id, items_count, items_json,
         total_value, time.time()))
    c.commit()


def set_next_update(steam_id: str, next_at: float):
    c = get_conn()
    c.execute(
        """INSERT INTO update_schedule (steam_id, next_update_at)
           VALUES (?, ?)
           ON CONFLICT(steam_id) DO UPDATE SET
           next_update_at=excluded.next_update_at""",
        (steam_id, next_at))
    c.commit()


def get_next_update(steam_id: str) -> float:
    r = get_conn().execute(
        "SELECT next_update_at FROM update_schedule WHERE steam_id=?",
        (steam_id,)).fetchone()
    return float(r[0]) if r else 0


def get_inventory(steam_id: str, app_id: int) -> dict:
    r = get_conn().execute(
        "SELECT * FROM inventory_cache "
        "WHERE steam_id=? AND app_id=?",
        (steam_id, app_id)).fetchone()
    return dict(r) if r else {}
