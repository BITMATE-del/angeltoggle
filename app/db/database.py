import sqlite3
from contextlib import contextmanager
from app.core.paths import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    phone TEXT,
    session_file TEXT UNIQUE,
    telegram_uid TEXT,
    username TEXT,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_error TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    phone TEXT,
    normalized_phone TEXT NOT NULL UNIQUE,
    telegram_uid TEXT,
    telegram_username TEXT,
    assigned_account_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING',
    error_code TEXT,
    error_message TEXT,
    telegram_message_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    postbot_link TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    account_id INTEGER,
    campaign_id INTEGER,
    recipient_id INTEGER,
    message TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

class Database:
    def __init__(self):
        self.path = str(DB_PATH)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self):
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    def execute(self, sql, params=()):
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def fetchall(self, sql, params=()):
        with self.connection() as conn:
            return list(conn.execute(sql, params).fetchall())

    def set_setting(self, key, value):
        self.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, str(value))
        )

    def get_setting(self, key, default=""):
        rows = self.fetchall("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default
