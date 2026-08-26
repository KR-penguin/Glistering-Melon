"""Payment state and the unpaid-server grace timer.

Payment arrives by bank transfer to a personal 경남은행 account, and there is
no programmatic way to see it: 오픈뱅킹 has no access category for individuals
at all. So confirmation is manual — the founder reads their bank app and tells
the Discord ops agent, which calls mark_paid(). PayApp (비사업자 개인 accepted,
real webhook) and RTPay (경남은행 push relay) are the upgrade paths; both would
call mark_paid() too, so nothing above this module changes when one lands.

The grace window is deliberately forgiving in its FAILURE mode rather than its
length. Korean transfers are usually instant, but the legitimate tail is long:
지연이체서비스 delays by a minimum of 3 hours, 은행 점검시간 clusters
23:30-00:30 (exactly when Minecraft buying peaks), 한도제한계좌 caps new and
student accounts daily, and buyers are often minors waiting on a parent. Every
Korean benchmark sits far higher than this window — 토스페이먼츠 가상계좌
defaults to 7 days, 카페24 10일, NHN 서버호스팅 7일. The founder chose 1 hour
with that evidence in hand, so the window is short by decision; these three
properties are what keep a short window from being destructive:

  1. Expiry STOPS the container. The world is never deleted here.
  2. A late deposit auto-restarts the server (mark_paid).
  3. The customer can self-serve one extension, which also pings Discord —
     the founder may simply be asleep when a real payment lands.
"""

import asyncio
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from app import discord_notify, docker_ctl, naming
from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

# Must match the pricing table in Landing.dc.html. Duplicated because the
# landing page is a static file with no access to this module; if you change a
# price, change it in both places.
TIER_PRICES = {"small": 4900, "friends": 11900, "community": 49000}

BANK_NAME = "경남은행"
BANK_ACCOUNT = "2070218854605"

EXTENSION_MINUTES = 120

SWEEP_INTERVAL_SECONDS = 60


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def price_for(tier: str) -> int:
    return TIER_PRICES[tier]


def grace_deadline(from_time: datetime | None = None) -> datetime:
    return (from_time or now()) + timedelta(minutes=settings.payment_grace_minutes)


def generate_payment_code(conn: sqlite3.Connection) -> str:
    """A 4-digit code the customer appends to their name when transferring.

    Four digits on purpose: Korean banks truncate 입금자명 at differing lengths
    (카카오뱅크 around 8 characters), so a long code gets cut off in exactly the
    case you need it. Uniqueness only has to hold among servers still awaiting
    payment — codes are recycled freely once paid.
    """
    taken = {
        row["payment_code"]
        for row in conn.execute(
            "SELECT payment_code FROM servers WHERE payment_status = 'awaiting'"
        )
        if row["payment_code"]
    }
    for _ in range(200):
        code = f"{secrets.randbelow(10000):04d}"
        if code not in taken:
            return code
    raise RuntimeError("could not allocate a free payment code")


def generate_session_token() -> str:
    return secrets.token_urlsafe(24)


def seconds_remaining(row: sqlite3.Row) -> int | None:
    """Seconds until the unpaid server is stopped. None once paid."""
    if row["payment_status"] == "paid":
        return None
    due = parse_iso(row["payment_due_at"])
    if due is None:
        return None
    return max(0, int((due - now()).total_seconds()))


def payment_summary(row: sqlite3.Row) -> dict:
    return {
        "status": row["payment_status"],
        "price_krw": row["price_krw"],
        "bank_name": BANK_NAME,
        "bank_account": BANK_ACCOUNT,
        "payment_code": row["payment_code"],
        "depositor_name_example": f"홍길동{row['payment_code']}" if row["payment_code"] else None,
        "seconds_remaining": seconds_remaining(row),
        "extension_used": bool(row["grace_extended"]),
    }


async def mark_paid(payment_code: str) -> dict:
    """Confirm a deposit. Restarts the server if the timer already stopped it.

    A late payment must be self-healing — Korean shops warn that manual refund
    queues are where late deposits go to die.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE payment_code = ? ORDER BY created_at DESC LIMIT 1",
            (payment_code,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"입금코드 {payment_code}에 해당하는 서버가 없습니다."}

        if row["payment_status"] == "paid":
            return {
                "ok": True,
                "already_paid": True,
                "nickname": row["nickname"],
                "message": f"{naming.owner_label(row)} 서버는 이미 입금 확인된 상태입니다.",
            }

        conn.execute(
            "UPDATE servers SET payment_status='paid', paid_at=?, suspended_at=NULL WHERE id=?",
            (_iso(now()), row["id"]),
        )
        conn.commit()

        restarted = False
        if row["status"] == "suspended":
            try:
                await asyncio.to_thread(docker_ctl.start_server, row["container_name"])
                conn.execute("UPDATE servers SET status='running' WHERE id=?", (row["id"],))
                conn.commit()
                restarted = True
            except Exception:
                logger.exception("failed to restart %s after payment", row["container_name"])
                return {
                    "ok": True,
                    "restarted": False,
                    "nickname": row["nickname"],
                    "message": f"{naming.owner_label(row)} 입금은 확인했지만 서버 재시작에 실패했습니다. 직접 확인이 필요합니다.",
                }

        return {
            "ok": True,
            "restarted": restarted,
            "nickname": row["nickname"],
            "message": (
                f"{naming.owner_label(row)} 입금 확인 완료. 정지됐던 서버를 다시 켰습니다."
                if restarted
                else f"{naming.owner_label(row)} 입금 확인 완료."
            ),
        }
    finally:
        conn.close()


async def request_extension(session_token: str) -> dict:
    """Customer says they've paid. Buys time for a human to confirm.

    Confirmation is manual, so the realistic failure is the founder being
    asleep, not the customer lying. One extension, then it stops.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE session_token = ?", (session_token,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "세션을 찾을 수 없습니다."}

        if row["payment_status"] == "paid":
            return {"ok": True, "message": "이미 입금이 확인된 서버입니다."}

        if row["grace_extended"]:
            await discord_notify.notify_payment_claim(
                naming.owner_label(row), row["payment_code"], row["price_krw"], repeat=True
            )
            return {
                "ok": False,
                "error": "연장은 한 번만 가능합니다. 운영자가 확인하는 대로 처리됩니다.",
            }

        # Extend from *now* by a fixed amount rather than from the original
        # deadline, so a short grace setting doesn't make the extension useless.
        new_due = now() + timedelta(minutes=EXTENSION_MINUTES)

        conn.execute(
            "UPDATE servers SET grace_extended=1, payment_due_at=?, payment_status='awaiting', suspended_at=NULL WHERE id=?",
            (_iso(new_due), row["id"]),
        )
        conn.commit()

        # If the sweeper already stopped it, bring it back while we wait.
        if row["status"] == "suspended":
            try:
                await asyncio.to_thread(docker_ctl.start_server, row["container_name"])
                conn.execute("UPDATE servers SET status='running' WHERE id=?", (row["id"],))
                conn.commit()
            except Exception:
                logger.exception("failed to restart %s on extension", row["container_name"])

        await discord_notify.notify_payment_claim(
            naming.owner_label(row), row["payment_code"], row["price_krw"], repeat=False
        )
        return {
            "ok": True,
            "message": f"확인 중입니다. {EXTENSION_MINUTES // 60}시간 더 열어뒀어요.",
            "seconds_remaining": EXTENSION_MINUTES * 60,
        }
    finally:
        conn.close()


async def sweep_overdue() -> int:
    """Stop servers whose payment window has passed. Never deletes."""
    conn = get_connection()
    stopped = 0
    try:
        rows = conn.execute(
            """
            SELECT * FROM servers
            WHERE payment_status = 'awaiting'
              AND status = 'running'
              AND payment_due_at IS NOT NULL
            """
        ).fetchall()

        for row in rows:
            due = parse_iso(row["payment_due_at"])
            if due is None or due > now():
                continue

            try:
                await asyncio.to_thread(docker_ctl.stop_server, row["container_name"])
            except Exception:
                logger.exception("failed to stop overdue server %s", row["container_name"])
                continue

            conn.execute(
                "UPDATE servers SET status='suspended', payment_status='expired', suspended_at=? WHERE id=?",
                (_iso(now()), row["id"]),
            )
            conn.commit()
            stopped += 1

            # The customer is not emailed: they find out from the session page
            # (status + countdown) or by asking in the support Discord.
            await discord_notify.notify_payment_expired(
                naming.owner_label(row), row["payment_code"], row["price_krw"]
            )
        return stopped
    finally:
        conn.close()


def session_url(token: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/session/{token}"


async def sweeper_loop() -> None:
    while True:
        try:
            await sweep_overdue()
        except Exception:
            logger.exception("payment sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
