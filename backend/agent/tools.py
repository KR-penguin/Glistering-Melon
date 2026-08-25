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

from agent.rag.store import available_versions, search_commands
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


def lookup_command(mc_version: str, query: str) -> dict:
    """Version-scoped command lookup.

    Always reports which version's reference the results came from and
    whether that was an exact match, so an approximate answer is never
    mistaken for an authoritative one.
    """
    return search_commands(mc_version, query)


def list_command_reference_versions() -> dict:
    return {
        "available_versions": available_versions(),
        "note": "Command syntax varies by version. Look up against the version the server is actually running.",
    }


async def get_server_version(server_id: str) -> dict:
    """Version actually running, not just the one recorded at provisioning.

    VERSION=LATEST resolves inside the container and a modpack pins its own
    Minecraft version, so the recorded value can drift from reality.
    """
    row = _get_server_row(server_id)
    if row is None:
        return {"error": f"no server with id {server_id}"}

    detected = await asyncio.to_thread(
        docker_ctl.detect_version_from_logs, row["container_name"]
    )

    if detected is None:
        note = (
            "Could not read the running version from the container logs "
            "(server may not have finished starting). The recorded version is "
            "a provisioning-time value, not proof of what is running."
        )
    elif detected != row["mc_version"]:
        note = (
            f"Running version ({detected}) differs from the recorded value "
            f"({row['mc_version']}). Use the running version for command lookups."
        )
    else:
        note = "Running version matches the recorded value."

    return {
        "server_id": server_id,
        "recorded_version": row["mc_version"],
        "running_version": detected,
        "modpack_key": row["modpack_key"],
        "note": note,
    }


async def read_server_logs(server_id: str, tail: int = 100) -> str:
    row = _get_server_row(server_id)
    if row is None:
        return f"error: no server with id {server_id}"
    return await asyncio.to_thread(docker_ctl.read_logs, row["container_name"], tail)


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
            "Search the console command reference for a specific Minecraft version. Command syntax "
            "changes between versions, so look up syntax here instead of relying on memory. The result "
            "reports which version's reference it came from and whether that exactly matched the version "
            "you asked for — if 'exact_version_match' is false, the syntax may not be valid on the "
            "running version, so say so and be cautious with destructive commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mc_version": {
                    "type": "string",
                    "description": "The version the server is ACTUALLY running, e.g. '1.21.1'. Get it from get_server_version, not from assumption.",
                },
                "query": {"type": "string", "description": "e.g. 'ban player', 'whitelist add'"},
            },
            "required": ["mc_version", "query"],
        },
    },
    {
        "name": "list_command_reference_versions",
        "description": "List which Minecraft versions have a bundled command reference available.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_server_version",
        "description": (
            "Report the Minecraft version a server is ACTUALLY running, read from its container logs, "
            "alongside the version recorded when it was provisioned. These can differ (VERSION=LATEST "
            "resolves at startup, and modpacks pin their own version). Use this before looking up "
            "command syntax on any server whose version you are not certain of."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"server_id": {"type": "string"}},
            "required": ["server_id"],
        },
    },
    {
        "name": "read_server_logs",
        "description": (
            "Read recent console output from a server's container. Works for any server type and keeps "
            "working when the Minecraft process is hung or RCON is unresponsive, because it reads the "
            "container's output rather than talking to the game. Use it to diagnose crashes, startup "
            "failures, or errors before deciding what to do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "tail": {"type": "integer", "description": "How many recent lines to read (default 100)"},
            },
            "required": ["server_id"],
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
    "list_command_reference_versions": lambda **kwargs: list_command_reference_versions(),
    "get_server_version": get_server_version,
    "read_server_logs": read_server_logs,
    "run_rcon_command": run_rcon_command,
    "start_server": start_server,
    "stop_server": stop_server,
    "restart_server": restart_server,
}
