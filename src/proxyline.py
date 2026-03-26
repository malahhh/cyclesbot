"""Proxyline API клиент."""

import logging
from typing import Optional

import httpx

from config import PROXYLINE_API_KEY

log = logging.getLogger("invest")

BASE = "https://panel.proxyline.net/api/"
HEADERS = {"Authorization": f"Token {PROXYLINE_API_KEY}"}


async def _get(endpoint: str, params: dict = None) -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}{endpoint}", headers=HEADERS,
                        params=params, timeout=15)
        r.raise_for_status()
        return r.json()


async def _post(endpoint: str, data: dict = None) -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}{endpoint}", headers=HEADERS,
                         json=data, timeout=15)
        r.raise_for_status()
        return r.json()


async def get_proxies() -> list:
    """Список всех прокси."""
    data = await _get("proxies/")
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return []


async def get_proxy(proxy_id: int) -> Optional[dict]:
    """Детали одного прокси."""
    try:
        return await _get(f"proxies/{proxy_id}/")
    except Exception as e:
        log.error("get_proxy %d: %s", proxy_id, e)
        return None


async def get_balance() -> float:
    """Баланс аккаунта."""
    try:
        data = await _get("balance/")
        return float(data.get("balance", 0))
    except Exception as e:
        log.error("get_balance: %s", e)
        return 0.0


async def renew_proxy(proxy_id: int, period: int = 30,
                      coupon: str = "") -> dict:
    """Продлить прокси."""
    payload = {"proxies": [proxy_id], "period": period}
    if coupon:
        payload["coupon"] = coupon
    return await _post("renew/", payload)


async def get_access_ips(proxy_id: int) -> list:
    """Получить список авторизованных IP."""
    try:
        data = await _get(f"proxies/{proxy_id}/access-ips/")
        if isinstance(data, list):
            return data
        return data.get("results", [])
    except Exception:
        return []


async def add_access_ip(proxy_id: int, ip: str) -> bool:
    """Добавить IP в whitelist прокси."""
    try:
        await _post(f"proxies/{proxy_id}/access-ips/",
                    {"ip": ip})
        return True
    except Exception as e:
        log.error("add_access_ip %d %s: %s", proxy_id, ip, e)
        return False


async def check_proxy(ip: str, port: int) -> bool:
    """Проверка доступности прокси (TCP connect)."""
    import asyncio
    try:
        _, w = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=5)
        w.close()
        await w.wait_closed()
        return True
    except Exception:
        return False
