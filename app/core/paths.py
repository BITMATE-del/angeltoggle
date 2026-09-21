from pathlib import Path
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = BASE_DIR / "sessions"
LOGS_DIR = BASE_DIR / "logs"
UPLOADS_DIR = BASE_DIR / "uploads"
EXPORTS_DIR = BASE_DIR / "exports"
for p in [DATA_DIR, SESSIONS_DIR, LOGS_DIR, UPLOADS_DIR, EXPORTS_DIR]:
    p.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "angeltoggle.db"
