"""In-game console commands via RCON — the separate, structurally distinct
path from docker_ctl.py's outside-the-container lifecycle control. Reached
over the internal `gm-net` Docker network by container name; never
published on the host.
"""

import asyncio
import logging
import time

from mcrcon import MCRcon

from app.docker_ctl import RCON_PORT_IN_CONTAINER

logger = logging.getLogger(__name__)


def _run_sync(container_name: str, password: str, command: str) -> str:
    with MCRcon(container_name, password, port=RCON_PORT_IN_CONTAINER) as client:
        return client.command(command)


async def run_command(container_name: str, password: str, command: str) -> str:
    return await asyncio.to_thread(_run_sync, container_name, password, command)


def _probe_sync(container_name: str, password: str) -> bool:
    try:
        with MCRcon(container_name, password, port=RCON_PORT_IN_CONTAINER, timeout=3):
            return True
    except Exception:
        return False


async def wait_until_ready(container_name: str, password: str, timeout_seconds: int = 120) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(_probe_sync, container_name, password):
            return True
        await asyncio.sleep(5)
    return False
