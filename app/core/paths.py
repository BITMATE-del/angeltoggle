import os
from pathlib import Path

APP_NAME = "AngelToggle"
base = os.environ.get("LOCALAPPDATA")
if base:
    BASE_DIR = Path(base) / APP_NAME
else:
    BASE_DIR = Path.home() / ".angeltoggle"

DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = BASE_DIR / "sessions"
LOGS_DIR = BASE_DIR / "logs"
UPLOADS_DIR = BASE_DIR / "uploads"
EXPORTS_DIR = BASE_DIR / "exports"

for p in [BASE_DIR, DATA_DIR, SESSIONS_DIR, LOGS_DIR, UPLOADS_DIR, EXPORTS_DIR]:
    p.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "angeltoggle.db"
