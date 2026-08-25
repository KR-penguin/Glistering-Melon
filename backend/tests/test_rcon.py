"""RCON protocol tests, against a fake server implementing Source RCON.

The thread test is the important one: the previous implementation used a
library that armed a SIGALRM for its timeout, which Python only permits on
the main thread. Since every RCON call here runs inside asyncio.to_thread,
that broke every command against every server. Keep it covered.
"""

import asyncio
import socket
import struct
import threading

import pytest

from agent import rcon_client
from agent.rcon_client import RconError, _pack, _run_sync


def _read_packet(conn):
    raw = b""
    while len(raw) < 4:
        raw += conn.recv(4 - len(raw))
    (size,) = struct.unpack("<i", raw)
    payload = b""
    while len(payload) < size:
        payload += conn.recv(size - len(payload))
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    return request_id, packet_type, payload[8:-2].decode()


class FakeRconServer:
    """Minimal Source RCON server: authenticates, echoes a canned reply."""

    def __init__(self, password="secret", reply="There are 2 of a max of 20 players online: a, b"):
        self.password = password
        self.reply = reply
        self.received_commands = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            try:
                req_id, ptype, body = _read_packet(conn)
                if ptype != rcon_client.TYPE_AUTH:
                    return
                if body != self.password:
                    conn.sendall(_pack(-1, rcon_client.TYPE_AUTH_RESPONSE, ""))
                    return
                conn.sendall(_pack(req_id, rcon_client.TYPE_AUTH_RESPONSE, ""))

                req_id, ptype, body = _read_packet(conn)
                self.received_commands.append(body)
                conn.sendall(_pack(req_id, rcon_client.TYPE_RESPONSE, self.reply))
            except (OSError, struct.error):
                return


def test_pack_frames_packet_correctly():
    packet = _pack(7, rcon_client.TYPE_EXEC, "list")
    (size,) = struct.unpack("<i", packet[:4])
    assert size == len(packet) - 4
    request_id, packet_type = struct.unpack("<ii", packet[4:12])
    assert (request_id, packet_type) == (7, rcon_client.TYPE_EXEC)
    assert packet.endswith(b"list\x00\x00")


def test_command_round_trip():
    with FakeRconServer() as server:
        out = _run_sync("127.0.0.1", "secret", "list", port=server.port)
    assert "2 of a max of 20" in out
    assert server.received_commands == ["list"]


def test_bad_password_raises():
    with FakeRconServer(password="right") as server:
        with pytest.raises(RconError, match="authentication failed"):
            _run_sync("127.0.0.1", "wrong", "list", port=server.port)


async def test_works_from_a_worker_thread():
    """Regression: the old mcrcon-based client raised
    'signal only works in main thread of the main interpreter' here,
    which is exactly how production calls it."""
    with FakeRconServer() as server:
        out = await asyncio.to_thread(
            _run_sync, "127.0.0.1", "secret", "list", 10.0, server.port
        )
    assert "players online" in out


async def test_run_command_helper_is_thread_offloaded():
    with FakeRconServer() as server:
        out = await rcon_client.run_command_on(
            "127.0.0.1", "secret", "list", port=server.port
        )
    assert "players online" in out


def test_connection_refused_surfaces_as_error():
    # Port 1 on loopback is not listening.
    with pytest.raises(OSError):
        _run_sync("127.0.0.1", "secret", "list", timeout=2.0, port=1)
