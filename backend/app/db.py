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


def get_connection() -> sqlite3.Connection:
    Path(settings.data_root).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
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
