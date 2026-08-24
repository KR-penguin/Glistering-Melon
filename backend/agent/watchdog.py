"""Non-LLM crash-restart notifier.

Crash recovery itself is Docker's own `restart_policy: unless-stopped` on
every server container (set in docker_ctl.create_and_start_server) — zero
LLM involvement, zero cost. This just watches for a restart Docker already
performed and posts a plain templated Discord message about it, so the
founder isn't left guessing why a server blipped.
"""

import asyncio
import logging

from app import discord_notify
from app.docker_ctl import get_client

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30

_last_restart_counts: dict[str, int] = {}


def _list_container_restart_counts() -> list[tuple[str, int]]:
    client = get_client()
    result = []
    for container in client.containers.list(all=True, filters={"name": "gm-"}):
        try:
            result.append((container.name, container.attrs.get("RestartCount", 0)))
        except Exception:
            continue
    return result


async def _poll_once() -> None:
    for name, restart_count in await asyncio.to_thread(_list_container_restart_counts):
        previous = _last_restart_counts.get(name)
        _last_restart_counts[name] = restart_count
        if previous is not None and restart_count > previous:
            await discord_notify.notify_watchdog_restart(name)


async def watch_containers() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("watchdog poll failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
