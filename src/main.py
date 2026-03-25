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
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(
        Path(__file__).resolve().parent.parent / "invest.log",
        encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main():
    setup_logging()
    log = logging.getLogger("invest")

    # Init DB + migration
    db.get_conn()
    migration.run()

    log.info("=" * 40)
    log.info("  Investment Bot v1.0")
    log.info("=" * 40)

    accs = db.get_accounts()
    log.info("Аккаунтов: %d", len(accs))

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
