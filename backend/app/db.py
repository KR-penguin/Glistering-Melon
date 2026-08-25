import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    email TEXT NOT NULL,
    tier TEXT NOT NULL,
    modpack_key TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS servers (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    nickname TEXT NOT NULL,
    tier TEXT NOT NULL,
    ram_mb INTEGER NOT NULL,
    storage_gb INTEGER NOT NULL,
    mc_version TEXT NOT NULL,
    mc_type TEXT NOT NULL,
    modpack_key TEXT,
    container_name TEXT NOT NULL,
    game_port INTEGER NOT NULL,
    rcon_password TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'provisioning',
    created_at TEXT NOT NULL
);
"""

# Columns added after the first deploy. CREATE TABLE IF NOT EXISTS silently
# does nothing on an existing table, so new columns have to be ALTERed in.
# Keep every entry nullable or defaulted — SQLite cannot add a NOT NULL column
# without a default to a table that already has rows.
_ADDED_COLUMNS = {
    "servers": [
        ("session_token", "TEXT"),
        ("email", "TEXT"),
        ("price_krw", "INTEGER"),
        ("payment_status", "TEXT NOT NULL DEFAULT 'awaiting'"),
        ("payment_code", "TEXT"),
        ("payment_due_at", "TEXT"),
        ("paid_at", "TEXT"),
        ("grace_extended", "INTEGER NOT NULL DEFAULT 0"),
        ("suspended_at", "TEXT"),
    ],
}

_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_servers_session_token ON servers(session_token)",
    "CREATE INDEX IF NOT EXISTS idx_servers_payment_code ON servers(payment_code)",
    "CREATE INDEX IF NOT EXISTS idx_servers_payment_status ON servers(payment_status)",
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def get_connection() -> sqlite3.Connection:
    Path(settings.data_root).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Bring a connection's database fully up to date.

    Single entry point on purpose: tests build their fixtures through this too,
    so the migration path is exercised rather than bypassed by a hand-written
    CREATE TABLE that quietly drifts from production.
    """
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    for statement in _INDEXES:
        conn.execute(statement)
    conn.commit()


def init_db() -> None:
    conn = get_connection()
    try:
        apply_schema(conn)
    finally:
        conn.close()


@contextmanager
def db_cursor(conn: sqlite3.Connection):
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
