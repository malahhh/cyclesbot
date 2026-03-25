"""Investment Bot — конфигурация."""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "694655175")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1001944485478")
CIRCLES_MSG_ID = int(os.getenv("CIRCLES_MSG_ID", "95"))
CS2_MSG_ID = int(os.getenv("CS2_MSG_ID", "98"))
SNIPER_DB_PATH = os.getenv("SNIPER_DB_PATH",
    "/home/openclawd/.openclaw/agents/architect/projects/"
    "lis-sniper/sniper.db")
DB_PATH = str(ROOT_DIR / "investment.db")
AUTHORIZED_USER = int(TELEGRAM_CHAT_ID)
