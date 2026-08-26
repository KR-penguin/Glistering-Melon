"""Plumbing tests (no LLM needed) plus the acceptance tests for the founder's
"act from outside the server" requirement.

The acceptance tests need ANTHROPIC_API_KEY. They cover the routing the
product cannot get wrong: a restart must never become an RCON command. The
executor now refuses that outright (see tests/test_rcon_interlock.py) — these
check the model gets there on its own, without leaning on the refusal.
"""

import sqlite3

import pytest

from agent import ops_agent, tools
from agent.rag import store
from app import docker_ctl
from app.config import settings
from app.db import SCHEMA


requires_llm = pytest.mark.skipif(
    not settings.google_cloud_project, reason="GOOGLE_CLOUD_PROJECT not set"
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

    log = {"rcon": [], "docker": [], "logs": []}

    async def fake_rcon_run(container_name, password, command):
        log["rcon"].append((container_name, command))
        return "ok"

    def fake_read_logs(container_name, tail=100):
        log["logs"].append((container_name, tail))
        return (
            "[init] Resolved version given LATEST into 1.21.4\n"
            "[Server thread/INFO]: Done (12.3s)! For help, type \"help\"\n"
        )

    monkeypatch.setattr(tools, "rcon_run_command", fake_rcon_run)
    monkeypatch.setattr("app.docker_ctl.read_logs", fake_read_logs)
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


async def test_read_server_logs(calls):
    out = await tools.read_server_logs("srv-1", tail=50)
    assert "Done (12.3s)" in out
    assert calls["logs"] == [("gm-test1234", 50)]


async def test_get_server_version_prefers_running_over_recorded(calls):
    # Container logs say 1.21.4; the DB row says 1.21.1.
    out = await tools.get_server_version("srv-1")
    assert out["running_version"] == "1.21.4"
    assert out["recorded_version"] == "1.21.1"
    assert "differs" in out["note"]


# --- version provenance: the whole point of the RAG layer ------------------


def test_lookup_command_exact_version_is_marked_exact():
    out = tools.lookup_command("1.21.1", "ban player")
    assert out["exact_version_match"] is True
    assert out["source_version"] == "1.21.1"
    assert any("ban" in r["command"] for r in out["results"])


def test_lookup_command_unknown_version_is_flagged_not_silent():
    """An unbundled version must come back visibly approximate.

    Silently answering from another version's reference is the exact
    failure this layer exists to prevent.
    """
    out = tools.lookup_command("1.99.9", "ban player")
    assert out["exact_version_match"] is False
    assert out["source_version"] in store.available_versions()
    assert "1.99.9" in out["note"]
    assert out["results"], "should still return best-effort results, just flagged"


def test_lookup_command_picks_nearest_reference_at_or_below():
    # 1.20.4 has no bundled reference; 1.20.1 is the newest at-or-below.
    out = tools.lookup_command("1.20.4", "whitelist")
    assert out["source_version"] == "1.20.1"
    assert out["exact_version_match"] is False


def test_available_versions_are_sorted_numerically():
    versions = store.available_versions()
    assert versions == sorted(versions, key=store._version_tuple)
    assert "1.21.1" in versions


# --- running-version detection from logs -----------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        ("[init] Resolved version given LATEST into 1.21.4", "1.21.4"),
        ("Starting minecraft server version 1.20.6", "1.20.6"),
        ("This server is running Paper (MC: 1.21.1)", "1.21.1"),
        ("nothing useful here", None),
    ],
)
def test_detect_version_from_logs(monkeypatch, line, expected):
    monkeypatch.setattr("app.docker_ctl.read_logs", lambda name, tail=2000: line)
    assert docker_ctl.detect_version_from_logs("whatever") == expected


def test_detect_version_returns_none_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(
        "app.docker_ctl.read_logs",
        lambda name, tail=2000: (_ for _ in ()).throw(RuntimeError("container gone")),
    )
    assert docker_ctl.detect_version_from_logs("whatever") is None


# --- full agent-loop acceptance tests (need a working LLM) -----------------


@requires_llm
async def test_ban_request_uses_rcon_not_docker(calls):
    reply = await ops_agent.handle_ops_message("srv-1 서버에서 Steve 밴 시켜줘.")
    assert calls["rcon"], f"expected an RCON call, got none. reply={reply}"
    assert not calls["docker"], f"ban must never touch Docker lifecycle, got {calls['docker']}"
    assert "ban" in calls["rcon"][0][1].lower()


@requires_llm
async def test_restart_request_uses_docker_not_rcon(calls):
    reply = await ops_agent.handle_ops_message("srv-1 서버 껐다 켜줘.")
    assert calls["docker"], f"expected a Docker lifecycle call, got none. reply={reply}"
    assert not calls["rcon"], f"restart must never go through RCON, got {calls['rcon']}"
    assert calls["docker"][0][0] == "restart"
