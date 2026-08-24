import asyncio
import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

from agent.rcon_client import wait_until_ready
from app import capacity, discord_notify, docker_ctl, email_notify
from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

_provision_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _application_email(conn: sqlite3.Connection, application_id: str) -> str | None:
    row = conn.execute("SELECT email FROM applications WHERE id=?", (application_id,)).fetchone()
    return row["email"] if row else None


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
                await email_notify.send_no_capacity_email(email, nickname)
                return {"status": "no_capacity", "application_id": application_id}

            server_id = str(uuid.uuid4())
            container_name = f"gm-{server_id[:8]}"
            game_port = docker_ctl.allocate_port(conn)
            rcon_password = secrets.token_urlsafe(18)
            spec = capacity.TIER_SPECS[tier]

            conn.execute(
                """INSERT INTO servers
                   (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
                    modpack_key, container_name, game_port, rcon_password, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'provisioning', ?)""",
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
                ),
            )
            conn.execute(
                "UPDATE applications SET status='provisioning' WHERE id=?", (application_id,)
            )
            conn.commit()

        asyncio.create_task(_provision_background(server_id))
        return {"status": "provisioning", "application_id": application_id, "server_id": server_id}
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

            await discord_notify.notify_provisioned(
                row["nickname"], row["container_name"], row["game_port"]
            )
            email = _application_email(conn, row["application_id"])
            if email:
                await email_notify.send_provisioned_email(
                    to=email,
                    nickname=row["nickname"],
                    game_address=f"<서버 도메인 또는 IP>:{row['game_port']}",
                )
        except Exception as exc:
            logger.exception("provisioning failed for server %s", server_id)
            conn.execute("UPDATE servers SET status='error' WHERE id=?", (server_id,))
            conn.commit()
            await discord_notify.notify_provision_failed(
                row["nickname"], row["container_name"], str(exc)
            )
            email = _application_email(conn, row["application_id"])
            if email:
                await email_notify.send_provision_failed_email(email, row["nickname"])
    finally:
        conn.close()
