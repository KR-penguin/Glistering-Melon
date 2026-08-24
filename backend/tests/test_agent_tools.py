"""Plumbing tests (no API key needed) plus the real acceptance tests for
the founder's "outside the server" requirement (need ANTHROPIC_API_KEY —
skipped automatically without one).
"""

import os
import sqlite3

import pytest

from agent import claude_client, tools
from app.db import SCHEMA

requires_anthropic = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
)


def make_conn_with_server() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        """INSERT INTO applications (id, nickname, email, tier, status, created_at)
           VALUES ('app-1', 'Steve', 's@example.com', 'small', 'provisioned', '2026-01-01')"""
    )
    conn.execute(
        """INSERT INTO servers
           (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
            container_name, game_port, rcon_password, status, created_at)
           VALUES ('srv-1', 'app-1', 'Steve', 'small', 2048, 15, '1.21.1', 'PAPER',
                   'gm-test1234', 25565, 'testpw', 'running', '2026-01-01')"""
    )
    conn.commit()
    return conn


@pytest.fixture
def calls(monkeypatch):
    conn = make_conn_with_server()
    monkeypatch.setattr(tools, "get_connection", lambda: conn)

    log = {"rcon": [], "docker": []}

    async def fake_rcon_run(container_name, password, command):
        log["rcon"].append((container_name, command))
        return "ok"

    monkeypatch.setattr(tools, "rcon_run_command", fake_rcon_run)
    monkeypatch.setattr(
        "app.docker_ctl.restart_server",
        lambda name: log["docker"].append(("restart", name)) or "restarted",
    )
    monkeypatch.setattr(
        "app.docker_ctl.start_server",
        lambda name: log["docker"].append(("start", name)) or "started",
    )
    monkeypatch.setattr(
        "app.docker_ctl.stop_server",
        lambda name: log["docker"].append(("stop", name)) or "stopped",
    )

    return log


# --- plumbing (always run, no API key needed) -------------------------------


async def test_run_rcon_command_resolves_container(calls):
    result = await tools.run_rcon_command("srv-1", "ban Steve")
    assert result == "ok"
    assert calls["rcon"] == [("gm-test1234", "ban Steve")]
    assert calls["docker"] == []


async def test_restart_server_goes_through_docker(calls):
    result = await tools.restart_server("srv-1")
    assert result == "restarted"
    assert calls["docker"] == [("restart", "gm-test1234")]
    assert calls["rcon"] == []


async def test_unknown_server_id_returns_error_not_exception(calls):
    result = await tools.restart_server("does-not-exist")
    assert "error" in result


def test_lookup_command_finds_ban_syntax():
    results = tools.lookup_command("1.21.1", "ban player")
    assert any("ban" in r["command"] for r in results)


# --- full agent-loop acceptance tests (need a real ANTHROPIC_API_KEY) -------


@requires_anthropic
async def test_ban_request_uses_rcon_not_docker(calls):
    reply = await claude_client.handle_message(
        "srv-1 서버에서 Steve 밴 시켜줘."
    )
    assert calls["rcon"], f"expected an RCON call, got none. reply={reply}"
    assert not calls["docker"], f"ban must never touch Docker lifecycle, got {calls['docker']}"
    assert "ban" in calls["rcon"][0][1].lower()


@requires_anthropic
async def test_restart_request_uses_docker_not_rcon(calls):
    reply = await claude_client.handle_message("srv-1 서버 껐다 켜줘.")
    assert calls["docker"], f"expected a Docker lifecycle call, got none. reply={reply}"
    assert not calls["rcon"], f"restart must never go through RCON, got {calls['rcon']}"
    assert calls["docker"][0][0] == "restart"
