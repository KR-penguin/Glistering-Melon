import asyncio
import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

from agent.rcon_client import wait_until_ready
from app import billing, capacity, discord_notify, docker_ctl
from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

_provision_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def submit_application(nickname: str, email: str, tier: str, modpack_key: str | None) -> dict:
    conn = get_connection()
    try:
        application_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO applications (id, nickname, email, tier, modpack_key, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'received', ?)""",
            (application_id, nickname, email, tier, modpack_key, _now()),
        )
        conn.commit()

        await discord_notify.notify_new_application(nickname, email, tier, modpack_key)

        async with _provision_lock:
            result = capacity.check_capacity(tier, conn)
            if not result.ok:
                conn.execute(
                    "UPDATE applications SET status='no_capacity' WHERE id=?", (application_id,)
                )
                conn.commit()
                await discord_notify.notify_capacity_alert(nickname, tier)
                return {"status": "no_capacity", "application_id": application_id}

            server_id = str(uuid.uuid4())
            container_name = f"gm-{server_id[:8]}"
            game_port = docker_ctl.allocate_port(conn)
            rcon_password = secrets.token_urlsafe(18)
            spec = capacity.TIER_SPECS[tier]
            session_token = billing.generate_session_token()
            payment_code = billing.generate_payment_code(conn)

            conn.execute(
                """INSERT INTO servers
                   (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
                    modpack_key, container_name, game_port, rcon_password, status, created_at,
                    session_token, email, price_krw, payment_status, payment_code, payment_due_at,
                    grace_extended)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'provisioning', ?,
                           ?, ?, ?, 'awaiting', ?, ?, 0)""",
                (
                    server_id,
                    application_id,
                    nickname,
                    tier,
                    spec["ram_mb"],
                    spec["storage_gb"],
                    settings.default_mc_version,
                    settings.default_mc_type,
                    modpack_key,
                    container_name,
                    game_port,
                    rcon_password,
                    _now(),
                    session_token,
                    email,
                    billing.price_for(tier),
                    payment_code,
                    billing.grace_deadline().isoformat(),
                ),
            )
            conn.execute(
                "UPDATE applications SET status='provisioning' WHERE id=?", (application_id,)
            )
            conn.commit()

        asyncio.create_task(_provision_background(server_id))
        return {
            "status": "provisioning",
            "application_id": application_id,
            "server_id": server_id,
            "session_token": session_token,
            "session_url": billing.session_url(session_token),
        }
    finally:
        conn.close()


async def _provision_background(server_id: str) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()
        if row is None:
            logger.error("provision_background: server %s not found", server_id)
            return

        try:
            await asyncio.to_thread(
                docker_ctl.create_and_start_server,
                container_name=row["container_name"],
                ram_mb=row["ram_mb"],
                mc_version=row["mc_version"],
                mc_type=row["mc_type"],
                nickname=row["nickname"],
                game_port=row["game_port"],
                rcon_password=row["rcon_password"],
                modpack_key=row["modpack_key"],
            )

            ready = await wait_until_ready(row["container_name"], row["rcon_password"])
            if not ready:
                raise RuntimeError("RCON did not become ready within timeout")

            conn.execute("UPDATE servers SET status='running' WHERE id=?", (server_id,))
            conn.commit()

            # No customer email is sent: the session page they were redirected
            # to already shows the address, price and deposit code, and polls
            # its own status.
            await discord_notify.notify_provisioned(
                row["nickname"], row["container_name"], row["game_port"]
            )
        except Exception as exc:
            logger.exception("provisioning failed for server %s", server_id)
            conn.execute("UPDATE servers SET status='error' WHERE id=?", (server_id,))
            conn.commit()
            await discord_notify.notify_provision_failed(
                row["nickname"], row["container_name"], str(exc)
            )
    finally:
        conn.close()
