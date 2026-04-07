#!/usr/bin/env python3
"""Investment Bot — точка входа."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db
import migration


def setup_logging():
    logger = logging.getLogger("invest")
    logger.setLevel(logging.DEBUG)  # DEBUG чтобы видеть HTTP детали
    fmt_full = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                 datefmt="%H:%M:%S")
    fmt_info = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                 datefmt="%H:%M:%S")

    root = Path(__file__).resolve().parent.parent

    # invest.log — INFO и выше (как раньше)
    fh = logging.FileHandler(root / "invest.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt_info)
    logger.addHandler(fh)

    # invest_debug.log — DEBUG и выше (HTTP детали, rate limit)
    fh_debug = logging.FileHandler(root / "invest_debug.log", encoding="utf-8")
    fh_debug.setLevel(logging.DEBUG)
    fh_debug.setFormatter(fmt_full)
    logger.addHandler(fh_debug)

    # Консоль — INFO
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt_info)
    logger.addHandler(sh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main():
    setup_logging()
    log = logging.getLogger("invest")

    # Init DB (миграция отключена — аккаунты добавляет пользователь)
    db.get_conn()

    log.info("=" * 40)
    log.info("  Investment Bot v1.0")
    log.info("=" * 40)

    inv = db.get_invest_accounts()
    cir = db.get_circle_accounts()
    log.info("Инвестиции: %d, Круги: %d", len(inv), len(cir))

    # Фоновый демон
    import daemon
    daemon.start()

    # TG бот
    from telegram.ext import Application
    import tg_handlers

    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    tg_handlers.setup_handlers(app)

    log.info("🤖 Telegram бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
