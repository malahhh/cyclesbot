"""Investment Bot — конфигурация."""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "694655175")
SNIPER_DB_PATH = os.getenv("SNIPER_DB_PATH",
    "/home/openclawd/.openclaw/agents/architect/projects/"
    "lis-sniper/sniper.db")
DB_PATH = str(ROOT_DIR / "investment.db")
AUTHORIZED_USER = int(TELEGRAM_CHAT_ID)
PROXYLINE_API_KEY = os.getenv("PROXYLINE_API_KEY",
    "k5emms8u35ztg4b1c445atk0q33pbhmt955vx46g")
