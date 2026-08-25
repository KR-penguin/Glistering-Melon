"""The one hard product guarantee: a lifecycle action can never reach RCON.

The agents run on a small local model that misroutes roughly one turn in 25,
and Korean phrasings like "서버 정지" genuinely read as both `/stop` and
`docker stop`. So this cannot be a prompt instruction — it has to be a refusal
in the executor that holds no matter what the model emits.
"""

import asyncio
import socket
import threading

import pytest

from agent import routing
from agent.rcon_client import (
    BLOCKED_VERBS,
    RconCommandBlocked,
    _pack,
    _run_sync,
    assert_allowed,
    command_verb,
    run_command,
)
from tests.test_rcon import FakeRconServer


# --- the interlock ---------------------------------------------------------


@pytest.mark.parametrize("command", sorted(BLOCKED_VERBS))
def test_every_lifecycle_verb_is_refused(command):
    with pytest.raises(RconCommandBlocked):
        assert_allowed(command)


@pytest.mark.parametrize(
    "command",
    [
        "stop",
        "/stop",
        "  /stop  ",
        "STOP",
        "Stop",
        "restart now",
        "/restart",
        "reload confirm",
        "/reload",
        "kill @a",
        "shutdown",
    ],
)
def test_refuses_regardless_of_slash_case_or_arguments(command):
    with pytest.raises(RconCommandBlocked):
        assert_allowed(command)


@pytest.mark.parametrize(
    "command",
    [
        "list",
        "ban Steve",
        "/ban Steve griefing",
        "whitelist add Alex",
        "kick Steve",
        "op Steve",
        "gamerule keepInventory true",
        "say 안녕하세요",
        "save-all",
        "time set day",
        "difficulty peaceful",
    ],
)
def test_ordinary_commands_pass(command):
    assert_allowed(command)  # must not raise


def test_stopsound_is_not_confused_with_stop():
    """Verb matching must be exact — `stopsound` is a legitimate command."""
    assert_allowed("stopsound @a")
    assert command_verb("stopsound @a") == "stopsound"


def test_kill_is_blocked_even_though_it_has_an_ingame_meaning():
    """`kill` can mean 'kill an entity', but it is also how you take a server
    down. Blocking it is the safe side of an ambiguous verb — the in-game
    entity case is rare in ops chat and has no recovery if we get it wrong."""
    with pytest.raises(RconCommandBlocked):
        assert_allowed("kill @e[type=zombie]")


def test_empty_command_does_not_crash():
    assert_allowed("")
    assert command_verb("") == ""


def test_blocked_message_points_at_the_right_tool():
    with pytest.raises(RconCommandBlocked, match="restart_server"):
        assert_allowed("/restart")


# --- enforced at the socket, not just at the helper ------------------------


def test_interlock_fires_before_any_connection_is_made():
    """Port 1 is not listening. If the interlock ran after connecting we'd get
    a connection error instead — proving the check happens first."""
    with pytest.raises(RconCommandBlocked):
        _run_sync("127.0.0.1", "pw", "/stop", timeout=2.0, port=1)


async def test_async_path_is_guarded_too():
    with FakeRconServer() as server:
        with pytest.raises(RconCommandBlocked):
            await run_command("127.0.0.1", "secret", "stop")
        assert server.received_commands == [], "nothing may reach the server"


async def test_a_legitimate_command_still_reaches_the_server():
    with FakeRconServer() as server:
        out = await asyncio.to_thread(
            _run_sync, "127.0.0.1", "secret", "list", 10.0, server.port
        )
    assert "players online" in out
    assert server.received_commands == ["list"]


# --- the Korean pre-router -------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["서버 껐다 켜줘", "재시작해줘", "서버 재부팅 해주세요", "리스타트 부탁해", "restart the server"],
)
def test_lifecycle_phrasings_are_classified(message):
    assert routing.classify(message) == routing.LIFECYCLE


@pytest.mark.parametrize(
    "message",
    ["철수 밴 시켜줘", "친구 화이트리스트에 넣어줘", "저 사람 킥해줘", "gamerule 바꿔줘"],
)
def test_ingame_phrasings_are_classified(message):
    assert routing.classify(message) == routing.INGAME


@pytest.mark.parametrize(
    "message",
    [
        "서버 상태 어때?",          # neither
        "리로드해줘",                # genuinely ambiguous — plugin or container?
        "밴 하고 서버도 재시작해줘",  # both families at once
    ],
)
def test_ambiguous_messages_are_left_to_the_model(message):
    assert routing.classify(message) is None
    assert routing.hint_for(message) == ""


def test_hint_text_names_the_right_tools():
    assert "restart_my_server" in routing.hint_for("재시작해줘")
    assert "RCON" in routing.hint_for("철수 밴 시켜줘")
    assert "restart_server" in routing.hint_for("재시작해줘", owner=True)
