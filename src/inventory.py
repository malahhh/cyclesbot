"""Steam Community API — загрузка инвентаря с пагинацией."""

import logging
import random
import time
from collections import Counter

import httpx

log = logging.getLogger("invest")

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101",
]


def get_inventory(steam_id: str, app_id: int = 730) -> list:
    """Загрузить инвентарь Steam с пагинацией.

    Returns: [{name: str, count: int}]
    """
    url = (f"https://steamcommunity.com/inventory/"
           f"{steam_id}/{app_id}/2")
    items = Counter()
    last_assetid = None

    for page in range(50):  # макс 50 страниц (25000 предметов)
        params = {"l": "english", "count": 500}
        if last_assetid:
            params["start_assetid"] = last_assetid

        try:
            r = httpx.get(url, params=params,
                          headers={"User-Agent": random.choice(
                              _USER_AGENTS)},
                          timeout=20)

            if r.status_code == 429:
                log.warning("Steam 429 — wait 30s")
                time.sleep(30)
                continue
            if r.status_code == 403:
                log.warning("Steam 403: %s (private?)", steam_id)
                return []
            if r.status_code != 200:
                log.warning("Steam %d: %s", r.status_code, steam_id)
                return []

            data = r.json()
            if not data.get("success"):
                break

            # Описания
            descs = {}
            for d in data.get("descriptions", []):
                key = f"{d['classid']}_{d.get('instanceid', '0')}"
                descs[key] = d.get("market_hash_name",
                                   d.get("name", "?"))

            # Assets
            assets = data.get("assets", [])
            for a in assets:
                key = f"{a['classid']}_{a.get('instanceid', '0')}"
                name = descs.get(key, "Unknown")
                count = int(a.get("amount", 1))
                items[name] += count

            log.info("  page %d: +%d assets (total: %d)",
                     page + 1, len(assets), sum(items.values()))

            # Пагинация
            if not data.get("more_items"):
                break
            last_assetid = data.get("last_assetid")
            if not last_assetid:
                break

            time.sleep(random.uniform(1.0, 3.0))

        except Exception as e:
            log.error("Steam inventory error %s: %s", steam_id, e)
            break

    result = [{"name": n, "count": c}
              for n, c in items.most_common()]
    log.info("Inventory %s app %d: %d unique, %d total",
             steam_id[:10], app_id,
             len(result), sum(i["count"] for i in result))
    return result
