"""The customer-facing tool set must be incapable of reaching another
tenant's server — not merely instructed not to.

The owner-facing tools in agent/tools.py take server_id as a model-supplied
argument. If a customer's agent had those, one tenant reaching another would
be a prompt-injection away. These tests assert the structural property that
replaces that trust: server_id is bound at construction and absent from every
tool schema.
"""

import sqlite3

import pytest

from agent import customer_agent
from tests.conftest import make_test_db


def make_db(tmp_path) -> str:
    path = make_test_db(str(tmp_path / "agent.db"))
    conn = sqlite3.connect(path)

    for sid, token, nickname, port in (("s1", "tok-a", "Alice", 25565), ("s2", "tok-b", "Bob", 25566)):
        conn.execute(
            "INSERT INTO applications (id, nickname, email, tier, status, created_at) "
            "VALUES (?, ?, 'x@y.com', 'small', 'provisioned', '2026-01-01')",
            (f"app-{sid}", nickname),
        )
        conn.execute(
            """INSERT INTO servers
               (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
                container_name, game_port, rcon_password, status, created_at,
                session_token, email, price_krw, payment_status, payment_code, payment_due_at,
                grace_extended)
               VALUES (?, ?, ?, 'small', 2048, 15, '1.21.1', 'PAPER',
                       ?, ?, 'pw', 'running', '2026-01-01', ?, 'x@y.com', 4900,
                       'awaiting', '1111', '2999-01-01T00:00:00+00:00', 0)""",
            (sid, f"app-{sid}", nickname, f"gm-{sid}", port, token),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = make_db(tmp_path)

    def connect():
        c = sqlite3.connect(path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(customer_agent, "get_connection", connect)

    calls = {"rcon": [], "restart": [], "stop": [], "start": []}

    async def fake_rcon(container, password, command):
        calls["rcon"].append((container, command))
        return "ok"

    monkeypatch.setattr(customer_agent, "rcon_run_command", fake_rcon)
    monkeypatch.setattr(customer_agent.docker_ctl, "restart_server",
                        lambda n: calls["restart"].append(n) or "restarted")
    monkeypatch.setattr(customer_agent.docker_ctl, "stop_server",
                        lambda n: calls["stop"].append(n) or "stopped")
    monkeypatch.setattr(customer_agent.docker_ctl, "start_server",
                        lambda n: calls["start"].append(n) or "started")
    return calls


# --- the security property -------------------------------------------------


def test_no_customer_tool_accepts_a_server_identifier():
    """If a schema ever grows a server_id, a customer could name someone
    else's server and only the model's goodwill would stop them."""
    for schema in customer_agent.TOOL_SCHEMAS:
        props = set(schema["input_schema"].get("properties", {}))
        leaked = {p for p in props if "server" in p.lower() or p in {"id", "container", "token"}}
        assert not leaked, f"{schema['name']} exposes {leaked}"


def test_customer_cannot_enumerate_other_servers():
    names = {s["name"] for s in customer_agent.TOOL_SCHEMAS}
    assert "list_servers" not in names


async def test_tools_are_bound_to_their_own_server(env):
    alice = customer_agent.build_tool_impls("s1")
    await alice["run_command_on_my_server"](command="whitelist add Carl")
    await alice["restart_my_server"]()

    assert env["rcon"] == [("gm-s1", "whitelist add Carl")]
    assert env["restart"] == ["gm-s1"]


async def test_two_sessions_stay_separated(env):
    alice = customer_agent.build_tool_impls("s1")
    bob = customer_agent.build_tool_impls("s2")

    await alice["restart_my_server"]()
    await bob["restart_my_server"]()

    assert env["restart"] == ["gm-s1", "gm-s2"]


async def test_extra_model_supplied_kwargs_are_ignored(env):
    """The model can emit unexpected arguments; they must not redirect the
    tool to another server."""
    alice = customer_agent.build_tool_impls("s1")
    await alice["restart_my_server"](server_id="s2", container_name="gm-s2")
    assert env["restart"] == ["gm-s1"]


# --- ordinary behavior -----------------------------------------------------


async def test_status_reports_price_and_address(env):
    tools = customer_agent.build_tool_impls("s1")
    status = await tools["get_my_server_status"]()
    assert status["running"] is True
    assert status["price_krw"] == 4900
    assert status["address"].endswith(":25565")
    assert status["payment"]["payment_code"] == "1111"


async def test_command_refused_when_server_is_off(env, tmp_path, monkeypatch):
    conn = customer_agent.get_connection()
    conn.execute("UPDATE servers SET status='suspended' WHERE id='s1'")
    conn.commit()
    conn.close()

    tools = customer_agent.build_tool_impls("s1")
    result = await tools["run_command_on_my_server"](command="list")
    assert "error" in result
    assert env["rcon"] == []


def test_session_lookup_by_token(env):
    assert customer_agent.get_server_by_token("tok-a")["nickname"] == "Alice"
    assert customer_agent.get_server_by_token("nope") is None
