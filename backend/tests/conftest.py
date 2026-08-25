import sqlite3

from app.db import apply_schema


def make_test_db(path: str) -> str:
    """Create a database through the real migration path, not a hand-rolled
    copy of the schema — a fixture that drifts from production hides bugs."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    conn.close()
    return path


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
