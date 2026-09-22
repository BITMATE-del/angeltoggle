import sqlite3
from contextlib import contextmanager
from app.core.paths import DB_PATH

RECIPIENTS_TABLE = """
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    import_id INTEGER,
    phone TEXT,
    normalized_phone TEXT NOT NULL,
    telegram_uid TEXT,
    telegram_username TEXT,
    contact_name TEXT,
    assigned_account_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING',
    contact_status TEXT NOT NULL DEFAULT 'NOT_ADDED',
    contact_added_at TEXT,
    error_code TEXT,
    error_message TEXT,
    telegram_message_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    sent_at TEXT
);
"""

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

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    total_count INTEGER NOT NULL DEFAULT 0,
    added_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    invalid_count INTEGER NOT NULL DEFAULT 0,
    duplicate_decision TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS import_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL,
    raw_phone TEXT,
    normalized_phone TEXT NOT NULL,
    reason TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(import_id) REFERENCES import_runs(id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    postbot_username TEXT NOT NULL DEFAULT '@PostBot',
    post_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    total_count INTEGER NOT NULL DEFAULT 0,
    contact_ready_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    contact_started_at TEXT,
    contact_finished_at TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    campaign_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    assigned_account_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ASSIGNED',
    contact_status TEXT NOT NULL DEFAULT 'WAITING',
    contact_added_at TEXT,
    sent_at TEXT,
    postbot_code TEXT,
    error_code TEXT,
    error_message TEXT,
    telegram_message_id TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (campaign_id, recipient_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_recipient_worker
ON campaign_recipients(campaign_id, assigned_account_id, status);

CREATE INDEX IF NOT EXISTS idx_recipient_phone
ON recipients(normalized_phone);

CREATE INDEX IF NOT EXISTS idx_recipient_status
ON recipients(status, contact_status);

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
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate_recipients_unique(self, conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recipients'"
        ).fetchone()
        if not row or not row[0] or "normalized_phone TEXT NOT NULL UNIQUE" not in row[0]:
            return

        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute("ALTER TABLE recipients RENAME TO recipients_legacy")
        conn.executescript(RECIPIENTS_TABLE)
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(recipients_legacy)").fetchall()]
        new_cols = [r[1] for r in conn.execute("PRAGMA table_info(recipients)").fetchall()]
        common = [c for c in old_cols if c in new_cols]
        cols = ",".join(common)
        conn.execute(f"INSERT INTO recipients({cols}) SELECT {cols} FROM recipients_legacy")
        conn.execute("DROP TABLE recipients_legacy")
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")

    def _ensure_columns(self, conn, table, definitions):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in definitions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def initialize(self):
        with self.connection() as conn:
            self._migrate_recipients_unique(conn)
            conn.executescript(RECIPIENTS_TABLE)
            conn.executescript(SCHEMA)
            self._ensure_columns(conn, "recipients", {
                "import_id": "INTEGER",
                "contact_status": "TEXT NOT NULL DEFAULT 'NOT_ADDED'",
                "contact_added_at": "TEXT",
                "contact_name": "TEXT",
            })
            self._ensure_columns(conn, "campaigns", {
                "contact_ready_count": "INTEGER NOT NULL DEFAULT 0",
                "contact_started_at": "TEXT",
                "contact_finished_at": "TEXT",
            })
            self._ensure_columns(conn, "campaign_recipients", {
                "contact_status": "TEXT NOT NULL DEFAULT 'WAITING'",
                "contact_added_at": "TEXT",
                "sent_at": "TEXT",
                "postbot_code": "TEXT",
            })

    def execute(self, sql, params=()):
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def execute_rowcount(self, sql, params=()):
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount

    def fetchall(self, sql, params=()):
        with self.connection() as conn:
            return list(conn.execute(sql, params).fetchall())

    def fetchone(self, sql, params=()):
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def set_setting(self, key, value):
        self.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, str(value))
        )

    def get_setting(self, key, default=""):
        row = self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default
