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
    def user_turn(text):
        return {"role": "user", "content": text}

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


def test_with_neither_backend_configured_the_error_names_both(monkeypatch):
    from app.config import settings

    llm.reset_provider()
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "google_cloud_project", "")
    with pytest.raises(llm.LLMError, match="GEMINI_API_KEY.*GOOGLE_CLOUD_PROJECT"):
        llm.get_provider()
    llm.reset_provider()


def test_an_api_key_selects_the_developer_backend(monkeypatch):
    from app.config import settings

    llm.reset_provider()
    monkeypatch.setattr(settings, "gemini_api_key", "AIza-not-a-real-key")
    monkeypatch.setattr(settings, "google_cloud_project", "")
    assert llm.get_provider().name == "gemini-api"
    llm.reset_provider()


def test_a_project_alone_selects_vertex(monkeypatch):
    from app.config import settings

    llm.reset_provider()
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "google_cloud_project", "some-project")
    assert llm.get_provider().name == "vertex-gemini"
    llm.reset_provider()


def test_the_key_wins_when_both_are_set(monkeypatch):
    """A half-finished cloud project should not shadow a key that works."""
    from app.config import settings

    llm.reset_provider()
    monkeypatch.setattr(settings, "gemini_api_key", "AIza-not-a-real-key")
    monkeypatch.setattr(settings, "google_cloud_project", "some-project")
    assert llm.get_provider().name == "gemini-api"
    llm.reset_provider()


# --- translating our tool schemas into Gemini's shape ----------------------
#
# The agents keep declaring tools in one shape; only this boundary knows what
# Gemini wants. Each test below is a request Gemini rejects outright if the
# translation is wrong, which in production reads as "the AI manager is down".


def test_parameterless_tool_omits_the_schema():
    """Gemini rejects `{"type": "object", "properties": {}}` as an empty
    declaration, and most customer tools take no arguments."""
    declarations = llm.GeminiProvider._function_declarations(
        [{"name": "get_my_server_status", "description": "상태",
          "input_schema": {"type": "object", "properties": {}}}]
    )
    assert declarations[0].name == "get_my_server_status"
    assert declarations[0].parameters_json_schema is None


def test_tool_with_arguments_keeps_its_schema():
    schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    declarations = llm.GeminiProvider._function_declarations(
        [{"name": "run_command", "description": "실행", "input_schema": schema}]
    )
    assert declarations[0].parameters_json_schema == schema


def test_tool_results_go_back_on_a_user_turn():
    """Gemini carries function results on a user turn; a "tool" role is
    rejected. The SDK's own loop does the same."""
    call = ToolCall("c1", "get_my_server_status", {})
    turns = llm.GeminiProvider.tool_results([(call, {"running": True})])
    assert len(turns) == 1
    assert turns[0].role == "user"
    assert turns[0].parts[0].function_response.name == "get_my_server_status"


def test_all_results_for_one_turn_are_batched_together():
    calls = [ToolCall("c1", "a", {}), ToolCall("c2", "b", {})]
    turns = llm.GeminiProvider.tool_results([(calls[0], {}), (calls[1], {})])
    assert len(turns) == 1 and len(turns[0].parts) == 2


def test_non_dict_tool_output_is_wrapped():
    """`response` must be a JSON object; a bare string is a 400."""
    turns = llm.GeminiProvider.tool_results([(ToolCall("c1", "a", {}), "plain text")])
    assert turns[0].parts[0].function_response.response == {"result": "plain text"}


async def test_a_safety_blocked_turn_does_not_500(monkeypatch):
    """Gemini can drop a whole turn to its safety filters, and this agent
    talks about banning and kicking all day. That arrives as an empty
    candidate list, not an exception."""

    class Blocked:
        candidates = []
        function_calls = None

    provider = llm.GeminiProvider.__new__(llm.GeminiProvider)

    class FakeModels:
        async def generate_content(self, **_):
            return Blocked()

    provider._client = type("C", (), {"aio": type("A", (), {"models": FakeModels()})()})()
    provider.model = "gemini-3.7-flash"

    reply = await provider.complete("sys", [], [])
    assert reply.text == "" and reply.tool_calls == []
