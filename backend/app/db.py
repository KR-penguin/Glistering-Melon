import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

SCHEMA = """
-- nickname and email are nullable: opening a server asks for nothing at all,
-- so an application starts anonymous and only gets a name and an address if
-- the customer later signs in with Google.
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    nickname TEXT,
    email TEXT,
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

-- password_hash is nullable because accounts are created by signing in with
-- Google, which never produces one. It stays in the schema so any account
-- made before the switch still resolves.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    google_sub TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
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
        # Null until the customer registers and claims the server.
        ("user_id", "TEXT"),
        # The name only when it is a real Java account; null otherwise.
        ("mc_username", "TEXT"),
    ],
    "users": [
        # Google's stable subject id. ALTER TABLE cannot add a UNIQUE column,
        # so uniqueness is enforced by the index in _INDEXES instead.
        ("google_sub", "TEXT"),
    ],
}

# Tables whose NOT NULL constraints were relaxed after the first deploy.
# SQLite cannot drop a NOT NULL in place — the only supported route is to
# rebuild the table and copy the rows across. Each entry is the table's
# current target definition, and the rebuild only fires when the live table
# still disagrees, so this is a no-op on an already-migrated database.
_RELAXED = {
    "applications": (
        ("nickname", "email"),
        """CREATE TABLE applications__new (
            id TEXT PRIMARY KEY,
            nickname TEXT,
            email TEXT,
            tier TEXT NOT NULL,
            modpack_key TEXT,
            status TEXT NOT NULL DEFAULT 'received',
            created_at TEXT NOT NULL
        )""",
    ),
    "users": (
        ("password_hash",),
        """CREATE TABLE users__new (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            created_at TEXT NOT NULL
        )""",
    ),
}

_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_servers_session_token ON servers(session_token)",
    "CREATE INDEX IF NOT EXISTS idx_servers_payment_code ON servers(payment_code)",
    "CREATE INDEX IF NOT EXISTS idx_servers_payment_status ON servers(payment_status)",
    "CREATE INDEX IF NOT EXISTS idx_servers_user ON servers(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)",
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _relax_not_null(conn: sqlite3.Connection) -> None:
    """Drop NOT NULL from columns that became optional, by rebuilding.

    Foreign keys are suspended for the swap: servers.application_id references
    applications(id), so dropping the old table would otherwise be refused.
    They go straight back on, and foreign_key_check runs before the change is
    kept — a rebuild that orphaned a server would be a silent data fault, and
    this is the one moment it could happen.
    """
    for table, (columns, create_sql) in _RELAXED.items():
        info = {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")}
        if not any(name in info and info[name]["notnull"] for name in columns):
            continue

        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute(create_sql)
            # Only columns present on both sides: the old table predates
            # whatever _ensure_columns has since added.
            new_columns = [
                row["name"] for row in conn.execute(f"PRAGMA table_info({table}__new)")
            ]
            shared = ", ".join(name for name in new_columns if name in info)
            conn.execute(f"INSERT INTO {table}__new ({shared}) SELECT {shared} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")

            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"rebuilding {table} would orphan {len(violations)} row(s)")
            conn.commit()
        except Exception:
            conn.rollback()
            conn.execute(f"DROP TABLE IF EXISTS {table}__new")
            conn.commit()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")


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
    # After the columns exist, so a rebuild carries them across rather than
    # dropping them and letting _ensure_columns re-add them empty.
    _relax_not_null(conn)
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
