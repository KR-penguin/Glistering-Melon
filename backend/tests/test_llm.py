"""Defenses in the shared tool loop.

Each test corresponds to a failure mode that would otherwise surface as a 500
to a paying customer: a malformed argument, an invented tool name, a tool that
raises, or a model that never stops calling tools.
"""

import json

import pytest

from agent import llm
from agent.llm import LLMReply, ToolCall, _loads_lenient, run_tool_loop


# --- lenient argument parsing ----------------------------------------------


def test_parses_normal_json_string():
    assert _loads_lenient('{"command": "list"}') == {"command": "list"}


def test_passes_through_a_dict():
    assert _loads_lenient({"command": "list"}) == {"command": "list"}


def test_empty_arguments_become_empty_dict():
    for empty in ("", None, "{}"):
        assert _loads_lenient(empty) == {}


def test_strips_prose_around_json():
    raw = 'Sure! Here you go: {"command": "whitelist add Steve"} hope that helps'
    assert _loads_lenient(raw) == {"command": "whitelist add Steve"}


def test_tolerates_trailing_comma():
    assert _loads_lenient('{"command": "list",}') == {"command": "list"}


def test_unparseable_arguments_do_not_raise():
    assert _loads_lenient("!!! not json at all") == {}


def test_korean_arguments_survive():
    assert _loads_lenient('{"command": "say 안녕하세요"}') == {"command": "say 안녕하세요"}


# --- the tool loop ---------------------------------------------------------


class FakeProvider:
    """Replays a scripted sequence of model replies."""

    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen_messages = []

    async def complete(self, system, messages, tools):
        self.seen_messages.append(list(messages))
        return self.replies.pop(0)

    @staticmethod
    def assistant_turn(reply):
        return {"role": "assistant", "content": reply.text}

    @staticmethod
    def tool_results(results):
        return [
            {"role": "tool", "tool_call_id": c.id, "name": c.name,
             "content": json.dumps(o, ensure_ascii=False, default=str)}
            for c, o in results
        ]


@pytest.fixture
def use_fake(monkeypatch):
    def install(replies):
        provider = FakeProvider(replies)
        monkeypatch.setattr(llm, "get_provider", lambda: provider)
        return provider

    return install


async def test_returns_text_when_no_tools_requested(use_fake):
    use_fake([LLMReply(text="서버는 정상입니다.")])
    out = await run_tool_loop("sys", "상태 알려줘", [], {})
    assert out == "서버는 정상입니다."


async def test_runs_a_tool_then_returns_the_final_text(use_fake):
    ran = []

    async def restart():
        ran.append("restart")
        return {"ok": True}

    use_fake([
        LLMReply(tool_calls=[ToolCall("c1", "restart_server", {})]),
        LLMReply(text="재시작했어요."),
    ])
    out = await run_tool_loop("sys", "재시작해줘", [], {"restart_server": restart})
    assert ran == ["restart"]
    assert out == "재시작했어요."


async def test_hallucinated_tool_name_is_reported_not_raised(use_fake):
    """Small models invent tools. That must come back as a tool result the
    model can recover from, not a 500."""
    provider = use_fake([
        LLMReply(tool_calls=[ToolCall("c1", "reboot_the_whole_box", {})]),
        LLMReply(text="그건 제가 할 수 없어요."),
    ])
    out = await run_tool_loop("sys", "재부팅", [], {"restart_server": lambda: {"ok": True}})
    assert out == "그건 제가 할 수 없어요."
    tool_msg = provider.seen_messages[-1][-1]
    assert "is not a tool" in tool_msg["content"]


async def test_bad_arguments_are_reported_not_raised(use_fake):
    def needs_command(command):
        return {"ok": True, "command": command}

    provider = use_fake([
        LLMReply(tool_calls=[ToolCall("c1", "run", {"wrong_kwarg": "x"})]),
        LLMReply(text="다시 시도했어요."),
    ])
    out = await run_tool_loop("sys", "명령", [], {"run": needs_command})
    assert out == "다시 시도했어요."
    assert "bad arguments" in provider.seen_messages[-1][-1]["content"]


async def test_tool_exception_is_reported_not_raised(use_fake):
    def boom():
        raise RuntimeError("rcon refused")

    provider = use_fake([
        LLMReply(tool_calls=[ToolCall("c1", "run", {})]),
        LLMReply(text="명령을 실행하지 못했어요."),
    ])
    out = await run_tool_loop("sys", "명령", [], {"run": boom})
    assert out == "명령을 실행하지 못했어요."
    assert "rcon refused" in provider.seen_messages[-1][-1]["content"]


async def test_loop_gives_up_with_fallback_text(use_fake):
    """A model that loops forever on tool calls must terminate, not hang."""
    use_fake([LLMReply(tool_calls=[ToolCall(f"c{i}", "noop", {})]) for i in range(10)])
    out = await run_tool_loop(
        "sys", "x", [], {"noop": lambda: {}}, max_iterations=3, fallback_text="포기했어요."
    )
    assert out == "포기했어요."


def test_missing_api_key_is_a_clear_error_not_a_crash(monkeypatch):
    from app.config import settings

    llm.reset_provider()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.get_provider()
    llm.reset_provider()
