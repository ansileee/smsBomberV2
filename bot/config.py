import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it to your .env file.")

# Turso (LibSQL) — persistent cloud DB that survives Railway restarts/redeploys
# Sign up free at https://turso.tech, create a DB, copy the URL and token here
TURSO_URL: str   = os.getenv("TURSO_URL", "")
TURSO_TOKEN: str = os.getenv("TURSO_TOKEN", "")

# Local SQLite fallback (dev only — not persistent on Railway)
_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE: str = os.path.join(_ROOT, "bot_data.db")

ADMIN_ID: int = 961369378
PROTECTED_NUMBER: str = "8075046930"
DEFAULT_DAILY_LIMIT: int = 10
DASHBOARD_UPDATE_INTERVAL: float = 2.0
PROXY_FILE: str = os.path.join(_ROOT, "proxies.txt")
DEFAULT_WORKERS: int = 4
IST_OFFSET_HOURS: float = 5.5

AI_BACKENDS = [
    "https://gpt-3-5.apis-bj-devs.workers.dev/?prompt={prompt}",
    "https://gemini-1-5-flash.bjcoderx.workers.dev/?text={prompt}",
    "https://deepseek-coder.apis-bj-devs.workers.dev/?text={prompt}",
    "https://qwen-ai.apis-bj-devs.workers.dev/?text={prompt}",
]