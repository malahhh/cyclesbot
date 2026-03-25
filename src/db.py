"""Investment Bot — SQLite БД."""

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
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            steam_id TEXT NOT NULL,
            amount TEXT DEFAULT '',
            scheme TEXT DEFAULT '',
            status TEXT DEFAULT 'buy',
            checked_at TEXT DEFAULT '',
            check_note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS inventory_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id),
            app_id INTEGER NOT NULL,
            items_count INTEGER DEFAULT 0,
            items_json TEXT DEFAULT '[]',
            total_value REAL DEFAULT 0,
            updated_at REAL DEFAULT 0,
            UNIQUE(account_id, app_id)
        );
        CREATE TABLE IF NOT EXISTS cs2_investments (
            name TEXT PRIMARY KEY,
            qty INTEGER DEFAULT 0,
            buy_price REAL DEFAULT 0,
            steam_price REAL DEFAULT 0,
            market_csgo_price REAL DEFAULT 0,
            updated_at REAL DEFAULT 0
        );
    """)
    c.commit()


# ============================================================
# Accounts CRUD
# ============================================================
def get_accounts() -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM accounts ORDER BY id").fetchall()]


def get_account(account_id: int) -> dict:
    r = get_conn().execute(
        "SELECT * FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    return dict(r) if r else {}


def get_account_by_login(login: str) -> dict:
    r = get_conn().execute(
        "SELECT * FROM accounts WHERE login=?", (login,)
    ).fetchone()
    return dict(r) if r else {}


def add_account(login: str, steam_id: str, **kw) -> int:
    c = get_conn()
    c.execute(
        """INSERT INTO accounts
           (login, steam_id, amount, scheme, status,
            checked_at, check_note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (login, steam_id, kw.get("amount", ""),
         kw.get("scheme", ""), kw.get("status", "buy"),
         kw.get("checked_at", ""), kw.get("check_note", "")))
    c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_account(account_id: int, **fields) -> bool:
    allowed = {"login", "steam_id", "amount", "scheme",
               "status", "checked_at", "check_note"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    vals.append(account_id)
    c = get_conn()
    c.execute(f"UPDATE accounts SET {','.join(sets)} WHERE id=?",
              vals)
    c.commit()
    return c.total_changes > 0


def delete_account(account_id: int) -> bool:
    c = get_conn()
    c.execute("DELETE FROM inventory_cache WHERE account_id=?",
              (account_id,))
    c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    c.commit()
    return c.total_changes > 0


# ============================================================
# Inventory cache
# ============================================================
def save_inventory(account_id: int, app_id: int,
                   items_count: int, items_json: str,
                   total_value: float):
    c = get_conn()
    c.execute(
        """INSERT INTO inventory_cache
           (account_id, app_id, items_count, items_json,
            total_value, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id, app_id) DO UPDATE SET
           items_count=excluded.items_count,
           items_json=excluded.items_json,
           total_value=excluded.total_value,
           updated_at=excluded.updated_at""",
        (account_id, app_id, items_count, items_json,
         total_value, time.time()))
    c.commit()


def get_inventory(account_id: int, app_id: int) -> dict:
    r = get_conn().execute(
        "SELECT * FROM inventory_cache "
        "WHERE account_id=? AND app_id=?",
        (account_id, app_id)).fetchone()
    return dict(r) if r else {}


def get_all_inventories(account_id: int) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM inventory_cache WHERE account_id=?",
        (account_id,)).fetchall()]


# ============================================================
# CS2 investments
# ============================================================
def save_cs2_investment(name: str, qty: int, buy_price: float,
                        steam_price: float = 0,
                        market_csgo_price: float = 0):
    c = get_conn()
    c.execute(
        """INSERT INTO cs2_investments
           (name, qty, buy_price, steam_price,
            market_csgo_price, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
           qty=excluded.qty, buy_price=excluded.buy_price,
           steam_price=excluded.steam_price,
           market_csgo_price=excluded.market_csgo_price,
           updated_at=excluded.updated_at""",
        (name, qty, buy_price, steam_price,
         market_csgo_price, time.time()))
    c.commit()


def get_cs2_investments() -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM cs2_investments ORDER BY name"
    ).fetchall()]
