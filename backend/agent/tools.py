"""Tools exposed to the Claude tool-calling loop (agent/claude_client.py).

Two structurally separate families, per the founder's explicit requirement
that the agent must be able to act from *outside* the server process:
- run_rcon_command: in-game console commands, via RCON (agent/rcon_client.py)
- start_server / stop_server / restart_server: Docker-level lifecycle
  control (app/docker_ctl.py) — works even if Minecraft is hung or RCON is
  unresponsive; not achievable through any in-game command.

No confirmation gate on any tool here (founder's explicit call) — the
agent executes directly once it selects a tool call.
"""

import asyncio
import logging

from agent.rag.store import search_commands
from agent.rcon_client import run_command as rcon_run_command
from app import docker_ctl
from app.db import get_connection

logger = logging.getLogger(__name__)


def _get_server_row(server_id: str):
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()
    finally:
        conn.close()


def list_servers() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, nickname, tier, mc_version, modpack_key, status FROM servers ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def lookup_command(mc_version: str, query: str) -> list[dict]:
    return search_commands(mc_version, query)


async def run_rcon_command(server_id: str, command: str) -> str:
    row = _get_server_row(server_id)
    if row is None:
        return f"error: no server with id {server_id}"
    return await rcon_run_command(row["container_name"], row["rcon_password"], command)


async def start_server(server_id: str) -> str:
    row = _get_server_row(server_id)
    if row is None:
        return f"error: no server with id {server_id}"
    return await asyncio.to_thread(docker_ctl.start_server, row["container_name"])


async def stop_server(server_id: str) -> str:
    row = _get_server_row(server_id)
    if row is None:
        return f"error: no server with id {server_id}"
    return await asyncio.to_thread(docker_ctl.stop_server, row["container_name"])


async def restart_server(server_id: str) -> str:
    row = _get_server_row(server_id)
    if row is None:
        return f"error: no server with id {server_id}"
    return await asyncio.to_thread(docker_ctl.restart_server, row["container_name"])


TOOL_SCHEMAS = [
    {
        "name": "list_servers",
        "description": "List all known Minecraft servers with their id, nickname, tier, version, and status.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lookup_command",
        "description": (
            "Search the command reference for a specific Minecraft version. Commands and their syntax "
            "change between versions, so always look up the exact syntax here before running an unusual "
            "command rather than relying on memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mc_version": {"type": "string", "description": "e.g. '1.21.1'"},
                "query": {"type": "string", "description": "e.g. 'ban player', 'whitelist add'"},
            },
            "required": ["mc_version", "query"],
        },
    },
    {
        "name": "run_rcon_command",
        "description": (
            "Run a raw console command on a running Minecraft server via RCON (ban, kick, op, whitelist, "
            "gamerule, etc). Requires the server's Minecraft process to be responsive. For restarting or "
            "power-cycling the server itself, use restart_server instead — this tool cannot do that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "command": {"type": "string", "description": "without the leading slash, e.g. 'ban Steve'"},
            },
            "required": ["server_id", "command"],
        },
    },
    {
        "name": "start_server",
        "description": "Start a stopped server at the Docker/process level, outside the game entirely.",
        "input_schema": {
            "type": "object",
            "properties": {"server_id": {"type": "string"}},
            "required": ["server_id"],
        },
    },
    {
        "name": "stop_server",
        "description": "Stop a server at the Docker/process level, outside the game entirely.",
        "input_schema": {
            "type": "object",
            "properties": {"server_id": {"type": "string"}},
            "required": ["server_id"],
        },
    },
    {
        "name": "restart_server",
        "description": (
            "Restart a server at the Docker/process level — works even if Minecraft is hung or RCON is "
            "unresponsive. Use this for any request to turn the server off and on / power-cycle it, never "
            "an RCON command."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"server_id": {"type": "string"}},
            "required": ["server_id"],
        },
    },
]

TOOL_IMPLS = {
    "list_servers": lambda **kwargs: list_servers(),
    "lookup_command": lambda **kwargs: lookup_command(**kwargs),
    "run_rcon_command": run_rcon_command,
    "start_server": start_server,
    "stop_server": stop_server,
    "restart_server": restart_server,
}
