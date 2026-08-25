"""Docker-level control of Minecraft server containers.

This is the "outside the server" tool family: start/stop/restart operate
at the container/process level via the Docker daemon, independent of
whether the Minecraft process itself (or RCON) is responsive. Never routes
through RCON — see agent/rcon_client.py for the separate in-game-command
path.
"""

import logging
import re
import sqlite3

import docker
from docker.errors import NotFound

from app.config import settings
from app.modpacks import MODPACK_CATALOG

logger = logging.getLogger(__name__)

NETWORK_NAME = "gm-net"
IMAGE = "itzg/minecraft-server:latest"
GAME_PORT_IN_CONTAINER = 25565
RCON_PORT_IN_CONTAINER = 25575


def get_client() -> docker.DockerClient:
    return docker.from_env()


def ensure_network(client: docker.DockerClient) -> None:
    try:
        client.networks.get(NETWORK_NAME)
    except NotFound:
        client.networks.create(NETWORK_NAME, driver="bridge")
        logger.info("created docker network %s", NETWORK_NAME)


def allocate_port(conn: sqlite3.Connection) -> int:
    used = {
        row["game_port"]
        for row in conn.execute(
            "SELECT game_port FROM servers WHERE status IN ('provisioning', 'running')"
        )
    }
    for port in range(settings.mc_port_range_start, settings.mc_port_range_end + 1):
        if port not in used:
            return port
    raise RuntimeError("no free game port in configured range")


def _build_env(
    *,
    ram_mb: int,
    mc_version: str,
    mc_type: str,
    nickname: str,
    rcon_password: str,
    modpack_key: str | None,
) -> dict:
    env = {
        "EULA": "TRUE",
        "MEMORY": f"{ram_mb}M",
        "VERSION": mc_version,
        "ENABLE_RCON": "true",
        "RCON_PASSWORD": rcon_password,
        "RCON_PORT": str(RCON_PORT_IN_CONTAINER),
        "ENABLE_WHITELIST": "true",
        "WHITELIST": nickname,
        "OVERRIDE_SERVER_PROPERTIES": "true",
    }
    if modpack_key:
        pack = MODPACK_CATALOG[modpack_key]
        env["TYPE"] = "MODRINTH"
        env["MODRINTH_MODPACK"] = pack.project
        env["MODRINTH_MODPACK_VERSION_TYPE"] = pack.version_type
        if pack.loader:
            env["MODRINTH_LOADER"] = pack.loader
        # VERSION above still narrows which MC version the modpack resolves to.
    else:
        env["TYPE"] = mc_type
    return env


def create_and_start_server(
    *,
    container_name: str,
    ram_mb: int,
    mc_version: str,
    mc_type: str,
    nickname: str,
    game_port: int,
    rcon_password: str,
    modpack_key: str | None,
):
    client = get_client()
    ensure_network(client)

    world_dir = settings.worlds_root / container_name
    world_dir.mkdir(parents=True, exist_ok=True)

    env = _build_env(
        ram_mb=ram_mb,
        mc_version=mc_version,
        mc_type=mc_type,
        nickname=nickname,
        rcon_password=rcon_password,
        modpack_key=modpack_key,
    )

    container = client.containers.run(
        IMAGE,
        name=container_name,
        detach=True,
        environment=env,
        network=NETWORK_NAME,
        ports={f"{GAME_PORT_IN_CONTAINER}/tcp": game_port},
        volumes={str(world_dir): {"bind": "/data", "mode": "rw"}},
        mem_limit=f"{ram_mb + 512}m",
        restart_policy={"Name": "unless-stopped"},
    )
    return container


def _get_container(client: docker.DockerClient, container_name: str):
    return client.containers.get(container_name)


def start_server(container_name: str) -> str:
    client = get_client()
    _get_container(client, container_name).start()
    return f"{container_name} started"


def stop_server(container_name: str) -> str:
    client = get_client()
    _get_container(client, container_name).stop(timeout=30)
    return f"{container_name} stopped"


def restart_server(container_name: str) -> str:
    client = get_client()
    _get_container(client, container_name).restart(timeout=30)
    return f"{container_name} restarted"


def container_status(container_name: str) -> str:
    client = get_client()
    try:
        return _get_container(client, container_name).status
    except NotFound:
        return "not_found"


def read_logs(container_name: str, tail: int = 100) -> str:
    """Read a server's console output from outside the game.

    Works regardless of server type (vanilla/Paper/modpack) and keeps
    working when the Minecraft process is hung or RCON is unresponsive,
    because it reads the container's stdout rather than talking to the
    game. Nothing is installed into the customer's server to enable this.
    """
    client = get_client()
    container = _get_container(client, container_name)
    raw = container.logs(tail=tail)
    return raw.decode("utf-8", errors="replace")


# The running version is not necessarily the one recorded at provisioning
# time: VERSION=LATEST resolves inside the container, and a Modrinth
# modpack pins its own Minecraft version. Command syntax varies by version,
# so the agent needs the version the server is ACTUALLY running.
_VERSION_PATTERNS = (
    re.compile(r"Resolved version given [^\s]+ into ([\d][\d.]*)"),
    re.compile(r"Starting minecraft server version ([\d][\d.]*)"),
    re.compile(r"\(MC:\s*([\d][\d.]*)\)"),
    re.compile(r"minecraft_version[\"'\s:=]+([\d][\d.]*)"),
)


def detect_version_from_logs(container_name: str) -> str | None:
    """Best-effort read of the actually-running Minecraft version.

    Returns None rather than guessing when the logs don't say — callers
    must treat None as "unknown", never as "assume the default".
    """
    try:
        text = read_logs(container_name, tail=2000)
    except Exception:
        return None

    # Later lines win: a modpack's resolved version is logged after the
    # generic startup banner.
    found = None
    for line in text.splitlines():
        for pattern in _VERSION_PATTERNS:
            m = pattern.search(line)
            if m:
                found = m.group(1)
    return found
