"""The tool-calling loop both agents run on, over the Claude API.

Normalised shapes so the agents don't touch provider details:

    ToolCall(id, name, arguments)   — what the model wants to run
    LLMReply(text, tool_calls)      — one turn of the conversation

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

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMError(Exception):
    pass


def _loads_lenient(raw: Any) -> dict:
    """Parse tool arguments that a small model may have mangled."""
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


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self.model = model

    async def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        import asyncio

        response = await asyncio.to_thread(
            self._client.messages.create,
            model=self.model,
            max_tokens=1024,
            system=system,
            tools=tools,
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=b.input or {})
            for b in response.content
            if b.type == "tool_use"
        ]
        self._last_content = response.content
        return LLMReply(text=text, tool_calls=calls)

    def assistant_turn(self, reply: LLMReply) -> dict:
        return {"role": "assistant", "content": self._last_content}

    @staticmethod
    def tool_results(results: list[tuple[ToolCall, Any]]) -> list[dict]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(output, ensure_ascii=False, default=str),
                    }
                    for call, output in results
                ],
            }
        ]


_provider = None


def get_provider():
    global _provider
    if _provider is None:
        if not settings.anthropic_api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set — the agent cannot run. "
                "Add it to backend/.env and restart."
            )
        _provider = AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
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

    Shared by the owner and customer agents so the small-model defenses live
    in exactly one place.
    """
    import asyncio

    provider = get_provider()
    allowed = set(tool_impls)
    messages: list[dict] = [{"role": "user", "content": first_message}]

    for _ in range(max_iterations):
        reply = await provider.complete(system, messages, tool_schemas)

        if not reply.wants_tools:
            return reply.text or fallback_text

        messages.append(provider.assistant_turn(reply))

        results: list[tuple[ToolCall, Any]] = []
        for call in reply.tool_calls:
            if call.name not in allowed:
                # Small models invent tool names. Tell the model rather than
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
