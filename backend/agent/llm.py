"""The tool-calling loop both agents run on, over Gemini on Google Vertex AI.

Normalised shapes so the agents don't touch provider details:

    ToolCall(id, name, arguments)   — what the model wants to run
    LLMReply(text, tool_calls, raw) — one turn of the conversation

`raw` carries the provider's own representation of the assistant turn, which
has to be echoed back verbatim on the next request: Gemini matches a function
response to its call by position and name in the history, so a reconstructed
turn is not good enough. Keeping it on the reply rather than on the provider
means two concurrent chats can never read each other's last turn.

The loop stays defensive even on a strong model, because the failure modes it
guards are cheap to handle and expensive to hit in production:

  * unknown tool names come back to the model as a tool result it can recover
    from, rather than raising,
  * arguments are parsed leniently,
  * a tool that raises is reported rather than killing the request,
  * the loop always terminates.

Note the guarantee that is NOT here: lifecycle commands cannot reach RCON.
That lives in agent/rcon_client.assert_allowed, enforced at the socket, so it
holds no matter which model runs or what it decides.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The provider-native assistant turn, replayed verbatim on the next call.
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMError(Exception):
    pass


def _loads_lenient(raw: Any) -> dict:
    """Parse tool arguments that a model may have mangled."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    text = str(raw).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        pass
    # Pull the outermost {...} out of any surrounding prose, then retry once
    # with trailing commas stripped.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidate = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    logger.warning("could not parse tool arguments: %r", text[:300])
    return {}


class VertexGeminiProvider:
    """Gemini on Vertex AI, via the google-genai SDK.

    Vertex authenticates with Application Default Credentials rather than an
    API key: in the container that means a service-account JSON mounted at
    GOOGLE_APPLICATION_CREDENTIALS. There is no key in .env to leak.
    """

    name = "vertex-gemini"

    def __init__(self, project: str, location: str, model: str):
        from google import genai

        # The SDK renamed this flag to `enterprise` when Vertex AI generative
        # became the Gemini Enterprise Agent Platform; `vertexai` is kept as a
        # supported alias and is what the GCP console and every Vertex doc
        # still call it, so it stays the more recognisable choice here.
        # requirements.txt pins google-genai <3 so the alias cannot vanish
        # under us.
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self.model = model

    @staticmethod
    def user_turn(text: str):
        from google.genai import types

        return types.Content(role="user", parts=[types.Part.from_text(text=text)])

    @staticmethod
    def _function_declarations(tool_schemas: list[dict]):
        """Translate the agents' Anthropic-shaped schemas into Gemini's.

        Only two things actually differ: the key name (`input_schema` ->
        `parameters_json_schema`), and that a parameterless tool must omit the
        schema entirely. Gemini rejects `{"type": "object", "properties": {}}`
        as an empty declaration, and more than half of the customer agent's
        tools take no arguments at all — so this is the difference between the
        tool set loading and the request 400ing.
        """
        from google.genai import types

        declarations = []
        for schema in tool_schemas:
            kwargs = {
                "name": schema["name"],
                "description": schema.get("description", ""),
            }
            params = schema.get("input_schema") or {}
            if params.get("properties"):
                kwargs["parameters_json_schema"] = params
            declarations.append(types.FunctionDeclaration(**kwargs))
        return declarations

    async def complete(self, system: str, messages: list, tools: list[dict]) -> LLMReply:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=1024,
            # We run our own loop, because the lifecycle interlock and the
            # unknown-tool recovery live in it. Letting the SDK call functions
            # itself would route around both.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            config.tools = [types.Tool(function_declarations=self._function_declarations(tools))]

        response = await self._client.aio.models.generate_content(
            model=self.model, contents=messages, config=config
        )

        candidates = response.candidates or []
        if not candidates or candidates[0].content is None:
            # Gemini's safety filters can drop a whole turn, and this agent
            # talks about banning and kicking players all day — vocabulary the
            # filters are sensitive to. A blocked turn is not an exception; it
            # comes back as an empty candidate list, and without this it would
            # surface as an IndexError 500 to a customer.
            logger.warning(
                "gemini returned no usable candidate (finish_reason=%s)",
                getattr(candidates[0], "finish_reason", None) if candidates else None,
            )
            return LLMReply(text="", tool_calls=[], raw=None)

        content = candidates[0].content
        parts = content.parts or []
        text = "".join(p.text for p in parts if getattr(p, "text", None))

        calls = [
            ToolCall(
                id=fc.id or f"call_{index}",
                name=fc.name,
                arguments=_loads_lenient(fc.args),
            )
            for index, fc in enumerate(response.function_calls or [])
        ]
        return LLMReply(text=text, tool_calls=calls, raw=content)

    @staticmethod
    def assistant_turn(reply: LLMReply):
        return reply.raw

    @staticmethod
    def tool_results(results: list[tuple[ToolCall, Any]]) -> list:
        """Every result for one assistant turn goes back in a single turn.

        Note the role is "user", not "tool" — Gemini carries function results
        on a user turn, and that is what the SDK's own function-calling loop
        emits. A "tool" role is rejected.
        """
        from google.genai import types

        return [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=call.name,
                        # The response field must be a JSON object. Tools here
                        # mostly return dicts, but not all of them do.
                        response=output if isinstance(output, dict) else {"result": output},
                    )
                    for call, output in results
                ],
            )
        ]


_provider = None


def get_provider():
    global _provider
    if _provider is None:
        if not settings.google_cloud_project:
            raise LLMError(
                "GOOGLE_CLOUD_PROJECT is not set — the agent cannot run. "
                "Add it to backend/.env and restart."
            )
        _provider = VertexGeminiProvider(
            settings.google_cloud_project,
            settings.google_cloud_location,
            settings.gemini_model,
        )
    return _provider


def reset_provider() -> None:
    """Test hook — drops the cached provider so settings changes take effect."""
    global _provider
    _provider = None


async def run_tool_loop(
    system: str,
    first_message: str,
    tool_schemas: list[dict],
    tool_impls: dict,
    max_iterations: int = 6,
    fallback_text: str = "요청을 처리하지 못했어요.",
) -> str:
    """Drive a multi-turn tool-calling conversation to a final text answer.

    Shared by the owner and customer agents so the defenses live in exactly
    one place.
    """
    import asyncio

    provider = get_provider()
    allowed = set(tool_impls)
    messages: list = [provider.user_turn(first_message)]

    for _ in range(max_iterations):
        reply = await provider.complete(system, messages, tool_schemas)

        if not reply.wants_tools:
            return reply.text or fallback_text

        messages.append(provider.assistant_turn(reply))

        results: list[tuple[ToolCall, Any]] = []
        for call in reply.tool_calls:
            if call.name not in allowed:
                # Models invent tool names. Tell the model rather than
                # crashing the request — it usually recovers on the next turn.
                logger.warning("model called unknown tool %r", call.name)
                results.append(
                    (call, {"error": f"'{call.name}' is not a tool. Available: {sorted(allowed)}"})
                )
                continue
            try:
                output = tool_impls[call.name](**call.arguments)
                if asyncio.iscoroutine(output):
                    output = await output
            except TypeError as exc:
                output = {"error": f"bad arguments for {call.name}: {exc}"}
            except Exception as exc:
                logger.exception("tool %s failed", call.name)
                output = {"error": str(exc)}
            results.append((call, output))

        messages.extend(provider.tool_results(results))

    return fallback_text
