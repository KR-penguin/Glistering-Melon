"""Claude tool-calling loop: turns a natural-language ops request from
Discord into real tool calls (RCON commands, Docker lifecycle actions,
version-specific command lookups). No confirmation gate — executes on
tool-selection, per the founder's explicit call.
"""

import asyncio
import json
import logging

from anthropic import Anthropic

from agent.tools import TOOL_IMPLS, TOOL_SCHEMAS
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the ops agent for Glistering Melon, a Minecraft server hosting \
service. You manage real Minecraft servers from OUTSIDE the game process.

## Version handling — this matters most

Minecraft console commands change between versions: syntax changes, \
commands are added, and some are removed. Your training data is not a \
reliable source for which syntax is valid on a given server. Follow this \
sequence before running any in-game command:

1. Identify the server (list_servers).
2. Establish the version it is ACTUALLY running (get_server_version). Do \
not assume, and do not trust the recorded value alone — VERSION=LATEST \
resolves at startup and modpacks pin their own version.
3. Look up the syntax for that version (lookup_command).
4. Read the result's `exact_version_match`. If it is false, the syntax came \
from a different version's reference and may be wrong. Say so in your reply, \
and prefer a safe read-only command (like `list`) to confirm behavior before \
running something destructive.
5. Then run it.

For a command you have just verified for that exact version, you may skip \
straight to running it. When in doubt, look it up — a wrong command on a \
live server with real players is worse than an extra lookup.

## In-game vs. outside-the-game

- run_rcon_command sends real in-game console commands (ban, kick, op, \
whitelist, gamerule, say, ...). It requires the Minecraft process to be \
responsive.
- start_server / stop_server / restart_server act at the Docker/process \
level, outside the game. They work even when the game is hung or RCON is \
dead. Any request to turn a server off/on, restart it, or recover an \
unresponsive server uses these — NEVER an RCON command.
- read_server_logs reads container output from outside the game. Use it to \
diagnose crashes, failed startups, or errors. When something is wrong, look \
at the logs before acting.

## General

- If a message doesn't clearly say which server it's about and more than one \
exists, call list_servers and ask a brief clarifying question rather than \
guessing.
- Execute directly once you've decided — do not ask for confirmation before \
running a tool.
- Reply in Korean, briefly, once you're done. State what you actually ran, \
and flag any version uncertainty you hit.\
"""

MAX_TOOL_ITERATIONS = 6

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


async def _call_tool(name: str, tool_input: dict):
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return f"error: unknown tool {name}"
    result = impl(**tool_input)
    if asyncio.iscoroutine(result):
        result = await result
    return result


async def handle_message(user_text: str) -> str:
    client = get_client()
    messages = [{"role": "user", "content": user_text}]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await asyncio.to_thread(
            client.messages.create,
            model=settings.anthropic_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            logger.info("agent tool call: %s(%s)", block.name, block.input)
            result = await _call_tool(block.name, block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return "요청을 처리하는 데 단계가 너무 많이 필요해서 중간에 멈췄어요. 다시 시도해 주세요."
