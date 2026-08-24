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

Rules:
- Minecraft console command syntax differs by version and changes over \
time. Before running any command you're not 100% certain of, call \
lookup_command with the server's mc_version first rather than relying on \
memory.
- To turn a server off/on, restart it, or act on it when it might be \
unresponsive, use start_server/stop_server/restart_server — these operate \
at the Docker/process level and work even if the game itself is hung. \
Never try to accomplish this with an RCON command.
- Use run_rcon_command only for real in-game console commands (ban, kick, \
op, whitelist, gamerule, etc) on a server that should be running.
- If a message doesn't clearly name which server it's about and more than \
one server exists, call list_servers and ask a brief clarifying question \
instead of guessing.
- Execute directly once you've decided what to do — do not ask for \
confirmation before running a tool. Reply in Korean, briefly, once you're \
done.\
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
