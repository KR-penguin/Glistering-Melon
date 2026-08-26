"""Migrating a database that predates the anonymous-signup change.

Opening a server stopped asking for a name and an email, which means
applications.nickname and applications.email had to stop being NOT NULL. And
signing in moved to Google, which never produces a password_hash. SQLite
cannot drop a NOT NULL in place, so both tables get rebuilt and the rows
copied — the one migration here that can silently lose customer data if it is
wrong, hence the coverage.
"""

import sqlite3

import pytest

from app.db import apply_schema

# The schema exactly as it shipped before this change, rows and all.
OLD_SCHEMA = """
CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    email TEXT NOT NULL,
    tier TEXT NOT NULL,
    modpack_key TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    created_at TEXT NOT NULL
);
CREATE TABLE servers (
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
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE auth_tokens (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def _notnull(conn, table, column) -> bool:
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row["name"] == column:
            return bool(row["notnull"])
    raise AssertionError(f"{table}.{column} does not exist")


@pytest.fixture
def old_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO applications VALUES ('app-1', '김철수', 'a@b.com', 'small', NULL, "
        "'provisioning', '2026-08-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO servers VALUES ('srv-1', 'app-1', '김철수', 'small', 2048, 15, '26.2', "
        "'PAPER', NULL, 'gm-abc', 25565, 'pw', 'running', '2026-08-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO users VALUES ('u-1', 'a@b.com', 'scrypt$hash', '2026-08-01T00:00:00+00:00')"
    )
    conn.commit()
    return conn


def test_existing_rows_survive_the_rebuild(old_db):
    apply_schema(old_db)

    app = old_db.execute("SELECT * FROM applications WHERE id='app-1'").fetchone()
    assert app["nickname"] == "김철수" and app["email"] == "a@b.com"
    assert app["tier"] == "small" and app["status"] == "provisioning"

    user = old_db.execute("SELECT * FROM users WHERE id='u-1'").fetchone()
    assert user["email"] == "a@b.com" and user["password_hash"] == "scrypt$hash"


def test_the_not_null_constraints_are_gone(old_db):
    apply_schema(old_db)
    assert not _notnull(old_db, "applications", "nickname")
    assert not _notnull(old_db, "applications", "email")
    assert not _notnull(old_db, "users", "password_hash")


def test_an_anonymous_application_inserts_after_migrating(old_db):
    """The whole point: opening a server no longer supplies either field."""
    apply_schema(old_db)
    old_db.execute(
        "INSERT INTO applications (id, tier, status, created_at) "
        "VALUES ('app-2', 'small', 'received', '2026-08-26T00:00:00+00:00')"
    )
    old_db.commit()
    row = old_db.execute("SELECT * FROM applications WHERE id='app-2'").fetchone()
    assert row["nickname"] is None and row["email"] is None


def test_the_server_still_points_at_its_application(old_db):
    """applications is dropped and recreated under a live foreign key."""
    apply_schema(old_db)
    old_db.execute("PRAGMA foreign_keys=ON")
    assert old_db.execute("PRAGMA foreign_key_check").fetchall() == []
    joined = old_db.execute(
        "SELECT a.tier FROM servers s JOIN applications a ON a.id = s.application_id "
        "WHERE s.id = 'srv-1'"
    ).fetchone()
    assert joined["tier"] == "small"


def test_foreign_keys_are_back_on_afterwards(old_db):
    apply_schema(old_db)
    assert old_db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_google_sub_is_added_to_an_old_users_table(old_db):
    apply_schema(old_db)
    columns = {row["name"] for row in old_db.execute("PRAGMA table_info(users)")}
    assert "google_sub" in columns


def test_google_sub_is_unique(old_db):
    apply_schema(old_db)
    old_db.execute(
        "INSERT INTO users (id, email, google_sub, created_at) "
        "VALUES ('u-2', 'c@d.com', 'sub-1', '2026-08-26T00:00:00+00:00')"
    )
    old_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        old_db.execute(
            "INSERT INTO users (id, email, google_sub, created_at) "
            "VALUES ('u-3', 'e@f.com', 'sub-1', '2026-08-26T00:00:00+00:00')"
        )


def test_two_accounts_may_both_have_no_google_sub(old_db):
    """A UNIQUE index has to keep tolerating multiple NULLs, or every
    pre-existing password account collides with the next one."""
    apply_schema(old_db)
    old_db.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES ('u-4', 'g@h.com', 'x', '2026-08-26T00:00:00+00:00')"
    )
    old_db.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES ('u-5', 'i@j.com', 'y', '2026-08-26T00:00:00+00:00')"
    )
    old_db.commit()


def test_migrating_twice_changes_nothing(old_db):
    apply_schema(old_db)
    apply_schema(old_db)
    assert old_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    assert old_db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert not _notnull(old_db, "applications", "email")


def test_a_fresh_database_needs_no_rebuild():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    assert not _notnull(conn, "applications", "email")
    assert not _notnull(conn, "users", "password_hash")
