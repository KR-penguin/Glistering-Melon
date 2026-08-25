"""In-game console commands via the Source RCON protocol.

This is the separate, structurally distinct path from docker_ctl.py's
outside-the-container lifecycle control. Reached over the internal
`gm-net` Docker network by container name; never published on the host.

Implemented directly rather than via the `mcrcon` package: that library
arms a SIGALRM for its read timeout, and Python only allows signal
handlers on the main thread. Every call here runs inside
`asyncio.to_thread` (so a slow server never stalls the Discord bot's
event loop), which made mcrcon raise "signal only works in main thread"
on every single call. Socket-level timeouts are thread-safe and give the
same protection.

Protocol reference: length-prefixed little-endian packets —
  int32 size | int32 request_id | int32 type | body NUL | NUL
Types: 3 = AUTH (out), 2 = AUTH_RESPONSE (in) / EXEC_COMMAND (out),
0 = RESPONSE_VALUE (in). An auth failure comes back as request_id -1.
"""

import asyncio
import logging
import socket
import struct
import time

from app.docker_ctl import RCON_PORT_IN_CONTAINER

logger = logging.getLogger(__name__)

TYPE_RESPONSE = 0
TYPE_EXEC = 2
TYPE_AUTH_RESPONSE = 2
TYPE_AUTH = 3

_AUTH_FAILED_ID = -1
_DEFAULT_TIMEOUT = 10.0
# Servers may split a long reply across packets. After the first one, keep
# draining briefly so multi-packet output isn't silently truncated.
_DRAIN_TIMEOUT = 0.3


class RconError(Exception):
    pass


class RconCommandBlocked(RconError):
    pass


# Lifecycle verbs never go through RCON. The product requirement is that
# "restart the server" ALWAYS acts at the container level — that is what works
# when the game is hung, which is exactly when it is needed.
#
# This lives in the executor rather than in a system prompt on purpose. The
# agents now run on a small local model that routes correctly around 92-97% of
# the time; at eight tools and ambiguous Korean ("서버 정지" reads as both
# `/stop` and `docker stop`) roughly one turn in 25 goes the wrong way. A
# prompt cannot make that never happen. A refusal here can, and it holds
# regardless of which model is configured, what the prompt says, or what a
# customer types into the chat box.
#
# Every one of these has a correct Docker-level counterpart in
# app/docker_ctl.py, so nothing legitimate is lost.
BLOCKED_VERBS = frozenset(
    {"stop", "restart", "reload", "shutdown", "halt", "kill", "end", "quit", "exit", "reboot"}
)


def command_verb(command: str) -> str:
    stripped = command.strip().lstrip("/")
    return stripped.split()[0].lower() if stripped.split() else ""


def assert_allowed(command: str) -> None:
    verb = command_verb(command)
    if verb in BLOCKED_VERBS:
        raise RconCommandBlocked(
            f"'{verb}' is a server-lifecycle action and must not run through RCON. "
            f"Use start_server / stop_server / restart_server instead — those work "
            f"even when the game is unresponsive."
        )


def _pack(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise RconError("connection closed by server")
        data += chunk
    return data


def _read_packet(sock: socket.socket) -> tuple[int, int, str]:
    (size,) = struct.unpack("<i", _recv_exact(sock, 4))
    if not (10 <= size <= 4_194_304):
        raise RconError(f"implausible RCON packet size: {size}")
    payload = _recv_exact(sock, size)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


def _authenticate(sock: socket.socket, password: str) -> None:
    sock.sendall(_pack(0, TYPE_AUTH, password))
    # Some servers emit an empty RESPONSE_VALUE ahead of the auth result.
    for _ in range(3):
        request_id, packet_type, _body = _read_packet(sock)
        if packet_type == TYPE_AUTH_RESPONSE:
            if request_id == _AUTH_FAILED_ID:
                raise RconError("RCON authentication failed (bad password)")
            return
    raise RconError("no RCON auth response from server")


def _run_sync(
    host: str,
    password: str,
    command: str,
    timeout: float = _DEFAULT_TIMEOUT,
    port: int = RCON_PORT_IN_CONTAINER,
) -> str:
    # Enforced at the socket, so no caller can bypass it.
    assert_allowed(command)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        _authenticate(sock, password)

        sock.sendall(_pack(1, TYPE_EXEC, command))
        _request_id, _packet_type, body = _read_packet(sock)

        parts = [body]
        sock.settimeout(_DRAIN_TIMEOUT)
        while True:
            try:
                _rid, _ptype, extra = _read_packet(sock)
            except (socket.timeout, TimeoutError, RconError):
                break
            except OSError:
                break
            parts.append(extra)

        return "".join(parts).strip()


async def run_command_on(
    host: str,
    password: str,
    command: str,
    timeout: float = _DEFAULT_TIMEOUT,
    port: int = RCON_PORT_IN_CONTAINER,
) -> str:
    return await asyncio.to_thread(_run_sync, host, password, command, timeout, port)


async def run_command(container_name: str, password: str, command: str) -> str:
    return await run_command_on(container_name, password, command)


def _probe_sync(host: str, password: str) -> bool:
    try:
        _run_sync(host, password, "list", timeout=3.0)
        return True
    except Exception:
        return False


async def wait_until_ready(container_name: str, password: str, timeout_seconds: int = 180) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(_probe_sync, container_name, password):
            return True
        await asyncio.sleep(5)
    return False
