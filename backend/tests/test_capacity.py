import sqlite3

import pytest

from app import config
from app.capacity import check_capacity


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE servers (id TEXT PRIMARY KEY, ram_mb INTEGER, storage_gb INTEGER, status TEXT)"
    )
    return conn


def insert_server(conn: sqlite3.Connection, server_id: str, ram_mb: int, storage_gb: int, status: str = "running") -> None:
    conn.execute(
        "INSERT INTO servers (id, ram_mb, storage_gb, status) VALUES (?, ?, ?, ?)",
        (server_id, ram_mb, storage_gb, status),
    )
    conn.commit()


@pytest.fixture(autouse=True)
def budget(monkeypatch):
    monkeypatch.setattr(config.settings, "host_ram_mb_budget", 8192)
    monkeypatch.setattr(config.settings, "host_storage_gb_budget", 200)
    monkeypatch.setattr(config.settings, "max_concurrent_servers", 5)


def test_ok_when_empty():
    result = check_capacity("small", make_conn())
    assert result.ok is True


def test_blocks_when_ram_exhausted(monkeypatch):
    monkeypatch.setattr(config.settings, "host_ram_mb_budget", 2048)
    conn = make_conn()
    insert_server(conn, "s1", ram_mb=2048, storage_gb=15)
    result = check_capacity("small", conn)
    assert result.ok is False


def test_ignores_stopped_and_error_servers(monkeypatch):
    monkeypatch.setattr(config.settings, "host_ram_mb_budget", 2048)
    conn = make_conn()
    insert_server(conn, "s1", ram_mb=2048, storage_gb=15, status="stopped")
    insert_server(conn, "s2", ram_mb=2048, storage_gb=15, status="error")
    result = check_capacity("small", conn)
    assert result.ok is True


def test_blocks_on_max_concurrent(monkeypatch):
    monkeypatch.setattr(config.settings, "max_concurrent_servers", 1)
    conn = make_conn()
    insert_server(conn, "s1", ram_mb=100, storage_gb=1)
    result = check_capacity("small", conn)
    assert result.ok is False


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        check_capacity("bogus", make_conn())
