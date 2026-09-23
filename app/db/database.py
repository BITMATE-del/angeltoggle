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
    handoff_status TEXT NOT NULL DEFAULT 'NONE',
    handoff_source_campaign_id INTEGER,
    handoff_from_account_id INTEGER,
    handoff_reason TEXT,
    handoff_count INTEGER NOT NULL DEFAULT 0,
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
    worker_sector INTEGER NOT NULL DEFAULT 1,
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

CREATE TABLE IF NOT EXISTS duplicate_check_db (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    raw_phone TEXT,
    normalized_phone TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_duplicate_check_db_phone
ON duplicate_check_db(normalized_phone);

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
    retry_of_campaign_id INTEGER,
    retry_round INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS assignment_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL,
    source_campaign_id INTEGER,
    target_campaign_id INTEGER,
    from_account_id INTEGER,
    to_account_id INTEGER,
    event_type TEXT NOT NULL,
    reason_code TEXT,
    reason_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_assignment_history_recipient
ON assignment_history(recipient_id, created_at);

CREATE TABLE IF NOT EXISTS retry_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL,
    source_campaign_id INTEGER NOT NULL,
    retry_campaign_id INTEGER NOT NULL,
    retry_round INTEGER NOT NULL DEFAULT 1,
    previous_status TEXT,
    previous_error_code TEXT,
    previous_error_message TEXT,
    result_status TEXT NOT NULL DEFAULT 'RETRY_WAITING',
    result_error_code TEXT,
    result_error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_retry_history_campaign
ON retry_history(retry_campaign_id, recipient_id);

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

CREATE TABLE IF NOT EXISTS telegram_check_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    api_task_id TEXT,
    total_input_count INTEGER NOT NULL DEFAULT 0,
    valid_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    invalid_count INTEGER NOT NULL DEFAULT 0,
    charged_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    filter_type TEXT NOT NULL DEFAULT 'ALL',
    amount_tenths_krw INTEGER NOT NULL DEFAULT 0,
    refund_tenths_krw INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    result_file_path TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_telegram_check_tasks_status
ON telegram_check_tasks(status, created_at);

CREATE TABLE IF NOT EXISTS telegram_check_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    raw_phone TEXT,
    normalized_phone TEXT,
    display_phone TEXT,
    input_status TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES telegram_check_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_check_inputs_task
ON telegram_check_inputs(task_id, input_status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_check_inputs_unique_valid
ON telegram_check_inputs(task_id, normalized_phone)
WHERE input_status='VALID';

CREATE TABLE IF NOT EXISTS telegram_check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    phone_number TEXT NOT NULL,
    telegram_check_status TEXT,
    telegram_id TEXT,
    telegram_username TEXT,
    telegram_active TEXT,
    raw_status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES telegram_check_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_check_results_task
ON telegram_check_results(task_id, phone_number);

CREATE TABLE IF NOT EXISTS telegram_check_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    batch_no INTEGER NOT NULL,
    api_task_id TEXT,
    item_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    total_count INTEGER NOT NULL DEFAULT 0,
    checked_count INTEGER NOT NULL DEFAULT 0,
    result_file_path TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(task_id, batch_no),
    FOREIGN KEY(task_id) REFERENCES telegram_check_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_check_batches_task
ON telegram_check_batches(task_id, batch_no);
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
                "handoff_status": "TEXT NOT NULL DEFAULT 'NONE'",
                "handoff_source_campaign_id": "INTEGER",
                "handoff_from_account_id": "INTEGER",
                "handoff_reason": "TEXT",
                "handoff_count": "INTEGER NOT NULL DEFAULT 0",
            })
            self._ensure_columns(conn, "campaigns", {
                "contact_ready_count": "INTEGER NOT NULL DEFAULT 0",
                "contact_started_at": "TEXT",
                "contact_finished_at": "TEXT",
                "retry_of_campaign_id": "INTEGER",
                "retry_round": "INTEGER NOT NULL DEFAULT 0",
            })
            self._ensure_columns(conn, "telegram_accounts", {
                "worker_sector": "INTEGER NOT NULL DEFAULT 1",
            })
            self._ensure_columns(conn, "campaign_recipients", {
                "contact_status": "TEXT NOT NULL DEFAULT 'WAITING'",
                "contact_added_at": "TEXT",
                "sent_at": "TEXT",
                "postbot_code": "TEXT",
            })

    def delete_recipients(self, recipient_ids):
        ids = sorted({int(x) for x in recipient_ids if x is not None})
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        with self.connection() as conn:
            campaign_rows = conn.execute(
                f"SELECT DISTINCT campaign_id FROM campaign_recipients "
                f"WHERE recipient_id IN ({placeholders})",
                ids,
            ).fetchall()
            campaign_ids = [int(r[0]) for r in campaign_rows]

            conn.execute(
                f"DELETE FROM campaign_recipients WHERE recipient_id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"DELETE FROM retry_history WHERE recipient_id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"DELETE FROM assignment_history WHERE recipient_id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"DELETE FROM work_logs WHERE recipient_id IN ({placeholders})",
                ids,
            )
            cur = conn.execute(
                f"DELETE FROM recipients WHERE id IN ({placeholders})",
                ids,
            )

            for campaign_id in campaign_ids:
                counts = conn.execute(
                    "SELECT COUNT(*) total, "
                    "SUM(CASE WHEN status='MESSAGE_SENT' THEN 1 ELSE 0 END) success, "
                    "SUM(CASE WHEN status IN ('FAILED','FAILED_FINAL') THEN 1 ELSE 0 END) failed "
                    "FROM campaign_recipients WHERE campaign_id=?",
                    (campaign_id,),
                ).fetchone()
                conn.execute(
                    "UPDATE campaigns SET total_count=?,success_count=?,failed_count=? "
                    "WHERE id=?",
                    (
                        int(counts["total"] or 0),
                        int(counts["success"] or 0),
                        int(counts["failed"] or 0),
                        campaign_id,
                    ),
                )

            return int(cur.rowcount or 0)

    def clear_customer_db(self):
        with self.connection() as conn:
            recipient_count = int(
                conn.execute("SELECT COUNT(*) FROM recipients").fetchone()[0] or 0
            )
            conn.execute("DELETE FROM campaign_recipients")
            conn.execute("DELETE FROM retry_history")
            conn.execute("DELETE FROM assignment_history")
            conn.execute("DELETE FROM recipients")
            conn.execute("DELETE FROM import_duplicates")
            conn.execute("DELETE FROM import_runs")
            conn.execute("DELETE FROM campaigns")
            conn.execute(
                "DELETE FROM work_logs WHERE recipient_id IS NOT NULL OR campaign_id IS NOT NULL"
            )
            return recipient_count

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
