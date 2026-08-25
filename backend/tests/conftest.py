import sqlite3

import pytest

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


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """TestClient over the real app, with the DB pointed at a temp dir.

    Deliberately does NOT enter the lifespan: startup reaches for the Docker
    daemon and starts background loops, neither of which a routing test wants.
    """
    from fastapi.testclient import TestClient

    from app.config import settings

    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    from app.main import app

    return TestClient(app)
