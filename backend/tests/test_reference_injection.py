"""The reference must reach the model, not merely be available to it.

As an optional tool it was consulted only when the model judged itself
unsure — which is exactly when a model is least reliable, since a confident
wrong answer skips the lookup entirely. These tests pin the retrieve-then-
generate behavior: documentation is in the prompt before the model runs.
"""

import pytest

from agent import customer_agent, llm, ops_agent
from agent.llm import LLMReply
from agent.rag import store


@pytest.fixture(autouse=True)
def fresh_index():
    store.reset_index()
    yield
    store.reset_index()


class CapturingProvider:
    name = "capture"

    def __init__(self):
        self.prompts = []

    async def complete(self, system, messages, tools):
        self.prompts.append({"system": system, "first": messages[0]["content"]})
        return LLMReply(text="네, 처리했어요.")

    @staticmethod
    def assistant_turn(reply):
        return {"role": "assistant", "content": reply.text}

    @staticmethod
    def tool_results(results):
        return []


@pytest.fixture
def capture(monkeypatch):
    provider = CapturingProvider()
    monkeypatch.setattr(llm, "get_provider", lambda: provider)
    return provider


# --- the block itself ------------------------------------------------------


def test_reference_block_contains_real_syntax_and_its_source():
    block = store.reference_block("26.2", "화이트리스트에 친구 추가해줘")
    assert "whitelist" in block
    assert "https://minecraft.wiki/w/" in block
    assert "26.2" in block


def test_reference_block_is_empty_for_an_unrelated_message():
    """An off-topic message must not burn context on command docs."""
    assert store.reference_block("26.2", "입금했어요") == ""


def test_reference_block_warns_when_the_version_is_not_an_exact_match():
    block = store.reference_block("26.3", "ban")
    assert "주의" in block


# --- Korean queries against an English reference ---------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("친구 화이트리스트에 넣어줘", "whitelist"),
        ("철수 밴 시켜줘", "ban"),
        ("저 사람 킥해줘", "kick"),
        ("지금 누가 접속해 있어?", "list"),
        ("낮으로 바꿔줘", "time"),
        ("난이도 쉽게 해줘", "difficulty"),
        ("날씨 맑게", "weather"),
        ("크리에이티브로 바꿔줘", "gamemode"),
        ("옵 줘", "op"),
    ],
)
def test_korean_messages_retrieve_the_right_command(message, expected):
    """The reference is English and customers write Korean. Without a bridge
    every real customer message would retrieve nothing, and the feature would
    look implemented while doing nothing."""
    out = store.search_commands("26.2", message)
    found = [r["command"] for r in out["results"]]
    assert expected in found, f"{message!r} retrieved {found}"


def test_command_name_outranks_a_body_mention():
    """/pardon's page discusses the ban list at length, so an unweighted
    search for "밴" ranked pardon above ban."""
    out = store.search_commands("26.2", "철수 밴 시켜줘")
    assert out["results"][0]["command"] == "ban"


def test_korean_particles_do_not_break_matching():
    """Korean agglutinates particles onto nouns, so matching must be on
    substrings: 화이트리스트에, 화이트리스트를, 화이트리스트는."""
    for suffix in ("에 추가", "를 켜줘", "는 어때"):
        out = store.search_commands("26.2", f"화이트리스트{suffix}")
        assert "whitelist" in [r["command"] for r in out["results"]]


def test_a_docker_lifecycle_request_retrieves_nothing():
    """"서버 껐다 켜줘" is a container action, not an in-game command — it must
    not pull command docs the agent might then try to run."""
    assert store.reference_block("26.2", "서버 껐다 켜줘") == ""


# --- the LATEST bug --------------------------------------------------------


def test_latest_does_not_resolve_to_the_oldest_reference():
    """Servers are created with VERSION=LATEST. Parsing that as a version
    yielded (0,) and selected the OLDEST docs, so a new 26.2 server would
    have been answered from 1.20.1."""
    out = store.search_commands("LATEST", "ban player")
    assert out["source_version"] == "26.2"
    assert out["exact_version_match"] is False


@pytest.mark.parametrize("value", ["LATEST", "SNAPSHOT", "", None, "latest"])
def test_non_version_pointers_all_fall_back_to_newest(value):
    assert store.search_commands(value, "ban")["source_version"] == "26.2"


def test_a_genuinely_old_version_still_gets_the_oldest_reference():
    out = store.search_commands("1.19.0", "ban")
    assert out["source_version"] == "1.20.1"
    assert out["exact_version_match"] is False


# --- injection into the customer agent -------------------------------------


@pytest.fixture
def server_row(monkeypatch):
    row = {
        "id": "srv-1", "nickname": "Steve", "tier": "small", "status": "running",
        "payment_status": "paid", "mc_version": "26.2", "container_name": "gm-1",
        "rcon_password": "pw", "game_port": 25565, "modpack_key": None,
        "price_krw": 4900, "payment_code": "1111", "grace_extended": 0,
        "payment_due_at": None, "session_token": "tok",
    }
    monkeypatch.setattr(customer_agent, "get_server_by_token", lambda t: row)
    monkeypatch.setattr(customer_agent, "build_tool_impls", lambda sid: {})
    return row


async def test_customer_agent_injects_the_reference(capture, server_row):
    await customer_agent.handle_customer_message("tok", "친구 화이트리스트에 넣어줘")
    prompt = capture.prompts[0]["first"]
    assert "[명령어 레퍼런스" in prompt
    assert "whitelist" in prompt
    assert "minecraft.wiki" in prompt


async def test_customer_agent_includes_the_server_version_in_context(capture, server_row):
    await customer_agent.handle_customer_message("tok", "안녕하세요")
    assert "26.2" in capture.prompts[0]["first"]


async def test_customer_agent_skips_the_reference_when_irrelevant(capture, server_row):
    await customer_agent.handle_customer_message("tok", "입금했어요")
    assert "[명령어 레퍼런스" not in capture.prompts[0]["first"]


async def test_customer_prompt_tells_the_model_to_trust_the_reference(capture, server_row):
    await customer_agent.handle_customer_message("tok", "밴 해줘")
    system = capture.prompts[0]["system"]
    assert "기억은" in system and "레퍼런스가 맞습니다" in system


# --- injection into the ops agent ------------------------------------------


async def test_ops_agent_injects_the_reference(capture):
    await ops_agent.handle_ops_message("Steve 밴 시켜줘")
    prompt = capture.prompts[0]["first"]
    assert "[명령어 레퍼런스" in prompt
    assert "minecraft.wiki" in prompt


async def test_ops_agent_skips_the_reference_when_irrelevant(capture):
    await ops_agent.handle_ops_message("지금 입금 대기 누구야?")
    assert "[명령어 레퍼런스" not in capture.prompts[0]["first"]
