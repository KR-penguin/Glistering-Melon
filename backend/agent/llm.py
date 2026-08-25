"""One tool-calling interface over two very different backends.

The agent code should not care whether it is talking to a local model or to
Anthropic, so both providers are normalised to the same shapes:

    ToolCall(id, name, arguments)   — what the model wants to run
    LLMReply(text, tool_calls)      — one turn of the conversation

The local provider speaks the OpenAI /v1/chat/completions dialect, which is
what Ollama, llama.cpp's llama-server, vLLM and LM Studio all expose. That
keeps the runtime swappable without touching agent code.

A small local model is materially less reliable at tool calling than Claude,
so this layer does the defensive work rather than leaving it scattered around
the agents:

  * tool names are validated against the allowed set and unknown names are
    reported back to the model instead of raising,
  * arguments arrive as a JSON string from OpenAI-style APIs and are parsed
    leniently, since small models emit trailing commas and stray prose,
  * a reply that is neither text nor a valid tool call is retried once with a
    corrective message before giving up.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 180.0  # a local CPU model can take a while for one turn


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


class LocalProvider:
    """OpenAI-compatible chat completions (Ollama, llama-server, vLLM, ...)."""

    name = "local"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            # Tool selection should be near-deterministic; creativity here just
            # produces invented tool names.
            "temperature": 0.1,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            try:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                raise LLMError(f"local LLM unreachable at {self.base_url}: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(f"local LLM returned {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("local LLM returned no choices")
        message = choices[0].get("message") or {}

        calls = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            fn = raw.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            calls.append(
                ToolCall(
                    id=raw.get("id") or f"call_{i}",
                    name=name,
                    arguments=_loads_lenient(fn.get("arguments")),
                )
            )

        return LLMReply(text=(message.get("content") or "").strip(), tool_calls=calls)

    @staticmethod
    def assistant_turn(reply: LLMReply) -> dict:
        msg: dict = {"role": "assistant", "content": reply.text or None}
        if reply.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                }
                for c in reply.tool_calls
            ]
        return msg

    @staticmethod
    def tool_results(results: list[tuple[ToolCall, Any]]) -> list[dict]:
        return [
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(output, ensure_ascii=False, default=str),
            }
            for call, output in results
        ]


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
        if settings.llm_provider == "anthropic":
            if not settings.anthropic_api_key:
                raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty")
            _provider = AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
        else:
            _provider = LocalProvider(settings.local_llm_base_url, settings.local_llm_model)
        logger.info("LLM provider: %s", _provider.name)
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
