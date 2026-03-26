"""Investment Bot — раздел Прокси."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from telegram import (Update, InlineKeyboardButton,
                      InlineKeyboardMarkup)
from telegram.ext import ContextTypes

import db
import proxyline

log = logging.getLogger("invest")
MSK = timezone(timedelta(hours=3))

# Кэш прокси (обновляется при запросе)
_proxy_cache: list = []
_cache_ts: float = 0


async def _get_proxies() -> list:
    """Получить список прокси (кэш 60с)."""
    global _proxy_cache, _cache_ts
    import time
    if time.time() - _cache_ts < 60 and _proxy_cache:
        return _proxy_cache
    try:
        _proxy_cache = await proxyline.get_proxies()
        _cache_ts = time.time()
    except Exception as e:
        log.error("get_proxies: %s", e)
    return _proxy_cache


def _find_proxy(proxies: list, proxy_id: int) -> dict | None:
    for p in proxies:
        if p.get("id") == proxy_id:
            return p
    return None


def _days_left(date_end: str) -> int:
    """Дней до истечения."""
    try:
        end = datetime.fromisoformat(date_end.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0, (end - now).days)
    except Exception:
        return -1


def _proxy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Привязать",
                              callback_data="px:bind"),
         InlineKeyboardButton("ℹ️ Инфо",
                              callback_data="px:info_pick")],
        [InlineKeyboardButton("🔓 IP авторизация",
                              callback_data="px:ip_pick"),
         InlineKeyboardButton("📊 Статус всех",
                              callback_data="px:status")],
    ])


async def show_proxy_section(update: Update,
                             ctx: ContextTypes.DEFAULT_TYPE):
    """Главный экран раздела Прокси."""
    bindings = db.get_all_proxy_bindings()
    proxies = await _get_proxies()

    lines = ["🌐 <b>Прокси</b>\n"]

    # Все аккаунты (invest + circle)
    all_logins = set()
    for a in db.get_invest_accounts():
        all_logins.add(a["login"])
    for a in db.get_circle_accounts():
        all_logins.add(a["login"])

    binding_map = {b["account_login"]: b for b in bindings}

    for login in sorted(all_logins):
        b = binding_map.get(login)
        if b:
            proxy = _find_proxy(proxies, b["proxy_id"])
            if proxy:
                ip = proxy.get("ip", "?")
                port = proxy.get("port_http", "?")
                country = proxy.get("country_name",
                                    proxy.get("country", "?"))
                date_end = proxy.get("date_end", "")
                days = _days_left(date_end)
                end_s = date_end[:10] if date_end else "?"
                warn = " ⚠️" if 0 < days <= 3 else ""
                lines.append(
                    f"🟦 {login} — {ip}:{port} "
                    f"({country}) — до {end_s}{warn}")
            else:
                lines.append(
                    f"🟦 {login} — proxy #{b['proxy_id']} "
                    f"(не найден в API)")
        else:
            lines.append(f"🟦 {login} — нет прокси")

    # Баланс
    try:
        balance = await proxyline.get_balance()
        lines.append(f"\n💵 Баланс Proxyline: ${balance:.2f}")
    except Exception:
        lines.append("\n💵 Баланс: ??")

    text = "\n".join(lines)
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=_proxy_kb())


async def on_proxy_callback(q, data: str,
                            ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка callback_data начинающихся с px:."""

    # --- Привязать ---
    if data == "px:bind":
        all_logins = _all_logins()
        rows = []
        row = []
        for login in all_logins:
            row.append(InlineKeyboardButton(
                login, callback_data=f"px:bind:{login}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(
            "🔙", callback_data="px:back")])
        await q.message.edit_text(
            "Выбери аккаунт для привязки прокси:",
            reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("px:bind:"):
        login = data[8:]
        ctx.user_data["flow"] = "px_bind"
        ctx.user_data["px_login"] = login
        await q.message.edit_text(
            f"🔗 Привязка прокси к <b>{login}</b>\n\n"
            f"Введи Proxy ID (число из Proxyline):",
            parse_mode="HTML")

    # --- Инфо ---
    elif data == "px:info_pick":
        bindings = db.get_all_proxy_bindings()
        if not bindings:
            await q.message.edit_text(
                "Нет привязанных прокси.",
                reply_markup=_proxy_kb())
            return
        rows = [[InlineKeyboardButton(
            f"ℹ️ {b['account_login']}",
            callback_data=f"px:info:{b['account_login']}")]
            for b in bindings]
        rows.append([InlineKeyboardButton(
            "🔙", callback_data="px:back")])
        await q.message.edit_text(
            "Выбери аккаунт:",
            reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("px:info:"):
        login = data[8:]
        b = db.get_proxy_binding(login)
        if not b:
            await q.message.edit_text("Прокси не привязан.")
            return
        proxy = await proxyline.get_proxy(b["proxy_id"])
        if not proxy:
            await q.message.edit_text(
                f"Прокси #{b['proxy_id']} не найден в API.",
                reply_markup=_proxy_kb())
            return
        ip = proxy.get("ip", "?")
        port_http = proxy.get("port_http", "?")
        port_socks = proxy.get("port_socks5", "?")
        user = proxy.get("user", "?")
        password = proxy.get("password", "?")
        country = proxy.get("country_name",
                            proxy.get("country", "?"))
        date_end = proxy.get("date_end", "?")[:10]
        days = _days_left(proxy.get("date_end", ""))

        text = (
            f"🌐 <b>Прокси: {login}</b>\n\n"
            f"IP: <code>{ip}</code>\n"
            f"HTTP: {port_http} | SOCKS5: {port_socks}\n"
            f"User: <code>{user}</code>\n"
            f"Pass: <code>{password}</code>\n"
            f"🌍 {country} | до {date_end} ({days} дн)\n\n"
            f"📋 <code>{ip}:{port_http}:{user}:{password}</code>")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Продлить 30д",
                                  callback_data=f"px:renew:{b['proxy_id']}"),
             InlineKeyboardButton("🔓 Добавить IP",
                                  callback_data=f"px:addip:{b['proxy_id']}")],
            [InlineKeyboardButton("❌ Отвязать",
                                  callback_data=f"px:unbind:{login}"),
             InlineKeyboardButton("🔙",
                                  callback_data="px:back")],
        ])
        await q.message.edit_text(text, parse_mode="HTML",
                                  reply_markup=kb)

    # --- Продлить ---
    elif data.startswith("px:renew:"):
        proxy_id = int(data.split(":")[2])
        await q.message.edit_text("🔄 Продлеваю на 30 дней...")
        try:
            result = await proxyline.renew_proxy(proxy_id, 30)
            await q.message.edit_text(
                f"✅ Продлено!\n{result}",
                reply_markup=_proxy_kb())
        except Exception as e:
            await q.message.edit_text(
                f"❌ Ошибка: {e}",
                reply_markup=_proxy_kb())

    # --- IP авторизация (выбор аккаунта) ---
    elif data == "px:ip_pick":
        bindings = db.get_all_proxy_bindings()
        if not bindings:
            await q.message.edit_text(
                "Нет привязанных прокси.",
                reply_markup=_proxy_kb())
            return
        rows = [[InlineKeyboardButton(
            f"🔓 {b['account_login']}",
            callback_data=f"px:addip:{b['proxy_id']}")]
            for b in bindings]
        rows.append([InlineKeyboardButton(
            "🔙", callback_data="px:back")])
        await q.message.edit_text(
            "Выбери прокси для IP авторизации:",
            reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("px:addip:"):
        proxy_id = int(data.split(":")[2])
        ctx.user_data["flow"] = "px_addip"
        ctx.user_data["px_proxy_id"] = proxy_id
        await q.message.edit_text(
            "🔓 Введи IP для добавления в whitelist:")

    # --- Отвязать ---
    elif data.startswith("px:unbind:"):
        login = data[10:]
        db.unbind_proxy(login)
        await q.message.edit_text(
            f"✅ Прокси отвязан от {login}",
            reply_markup=_proxy_kb())

    # --- Статус всех ---
    elif data == "px:status":
        await q.message.edit_text("📊 Проверяю прокси...")
        bindings = db.get_all_proxy_bindings()
        proxies = await _get_proxies()
        lines = ["📊 <b>Статус прокси</b>\n"]

        for b in bindings:
            proxy = _find_proxy(proxies, b["proxy_id"])
            if not proxy:
                lines.append(f"❌ {b['account_login']} — "
                             f"#{b['proxy_id']} не найден")
                continue
            ip = proxy.get("ip", "?")
            port = proxy.get("port_http", 0)
            days = _days_left(proxy.get("date_end", ""))
            alive = await proxyline.check_proxy(ip, int(port))
            status = "✅" if alive else "❌"
            warn = " ⚠️ ИСТЕКАЕТ!" if 0 < days <= 3 else ""
            lines.append(
                f"{status} {b['account_login']} — "
                f"{ip}:{port} ({days}д){warn}")

        if not bindings:
            lines.append("Нет привязанных прокси.")

        await q.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=_proxy_kb())

    # --- Назад ---
    elif data == "px:back":
        # Пересоздаём текст
        bindings = db.get_all_proxy_bindings()
        proxies = await _get_proxies()
        lines = ["🌐 <b>Прокси</b>\n"]
        all_logins = _all_logins()
        binding_map = {b["account_login"]: b for b in bindings}

        for login in all_logins:
            b = binding_map.get(login)
            if b:
                proxy = _find_proxy(proxies, b["proxy_id"])
                if proxy:
                    ip = proxy.get("ip", "?")
                    port = proxy.get("port_http", "?")
                    country = proxy.get("country_name", "?")
                    date_end = proxy.get("date_end", "")[:10]
                    lines.append(
                        f"🟦 {login} — {ip}:{port} "
                        f"({country}) — до {date_end}")
                else:
                    lines.append(
                        f"🟦 {login} — #{b['proxy_id']} (?)")
            else:
                lines.append(f"🟦 {login} — нет прокси")

        try:
            bal = await proxyline.get_balance()
            lines.append(f"\n💵 Баланс: ${bal:.2f}")
        except Exception:
            pass

        await q.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=_proxy_kb())


async def handle_proxy_text(update: Update,
                            ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обработка текстового ввода для Прокси. Returns True if handled."""
    flow = ctx.user_data.get("flow")
    text = update.message.text.strip()

    if flow == "px_bind":
        login = ctx.user_data.pop("px_login", "")
        ctx.user_data.clear()
        try:
            proxy_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ Введи число (Proxy ID)")
            return True
        db.bind_proxy(login, proxy_id)
        await update.message.reply_text(
            f"✅ Прокси #{proxy_id} привязан к {login}",
            reply_markup=_proxy_kb())
        return True

    elif flow == "px_addip":
        proxy_id = ctx.user_data.pop("px_proxy_id", 0)
        ctx.user_data.clear()
        if proxy_id:
            ok = await proxyline.add_access_ip(proxy_id, text)
            if ok:
                await update.message.reply_text(
                    f"✅ IP {text} добавлен в whitelist",
                    reply_markup=_proxy_kb())
            else:
                await update.message.reply_text(
                    f"❌ Ошибка добавления IP",
                    reply_markup=_proxy_kb())
        return True

    return False


def _all_logins() -> list:
    """Все логины из invest + circle."""
    logins = set()
    for a in db.get_invest_accounts():
        logins.add(a["login"])
    for a in db.get_circle_accounts():
        logins.add(a["login"])
    return sorted(logins)
