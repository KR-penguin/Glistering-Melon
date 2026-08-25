from datetime import timedelta

import pytest

from app import billing
from tests.conftest import connect, make_test_db


def make_db(tmp_path) -> str:
    return make_test_db(str(tmp_path / "billing.db"))


def insert_server(path, *, sid="s1", status="running", payment_status="awaiting",
                  due_offset_minutes=60, code="1234", token="tok1", extended=0):
    conn = connect(path)
    conn.execute(
        "INSERT INTO applications (id, nickname, email, tier, status, created_at) "
        "VALUES (?, 'Steve', 'a@b.com', 'small', 'provisioning', '2026-01-01')",
        (f"app-{sid}",),
    )
    due = billing.now() + timedelta(minutes=due_offset_minutes)
    conn.execute(
        """INSERT INTO servers
           (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
            container_name, game_port, rcon_password, status, created_at,
            session_token, email, price_krw, payment_status, payment_code, payment_due_at,
            grace_extended)
           VALUES (?, ?, 'Steve', 'small', 2048, 15, '1.21.1', 'PAPER',
                   ?, 25565, 'pw', ?, '2026-01-01', ?, 'a@b.com', 4900, ?, ?, ?, ?)""",
        (sid, f"app-{sid}", f"gm-{sid}", status, token, payment_status, code,
         due.isoformat(), extended),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = make_db(tmp_path)
    monkeypatch.setattr(billing, "get_connection", lambda: connect(path))

    calls = {"stopped": [], "started": []}
    monkeypatch.setattr(billing.docker_ctl, "stop_server",
                        lambda name: calls["stopped"].append(name) or "stopped")
    monkeypatch.setattr(billing.docker_ctl, "start_server",
                        lambda name: calls["started"].append(name) or "started")

    async def noop(*a, **k):
        return None

    for fn in ("notify_payment_claim", "notify_payment_expired"):
        monkeypatch.setattr(billing.discord_notify, fn, noop)
    monkeypatch.setattr(billing.email_notify, "send_payment_expired_email", noop)

    return {"path": path, "calls": calls}


def row(path, sid="s1"):
    conn = connect(path)
    try:
        return conn.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
    finally:
        conn.close()


# --- the grace timer -------------------------------------------------------


async def test_sweep_leaves_servers_inside_the_window_alone(db):
    insert_server(db["path"], due_offset_minutes=30)
    assert await billing.sweep_overdue() == 0
    assert db["calls"]["stopped"] == []
    assert row(db["path"])["status"] == "running"


async def test_sweep_stops_overdue_server(db):
    insert_server(db["path"], due_offset_minutes=-1)
    assert await billing.sweep_overdue() == 1
    assert db["calls"]["stopped"] == ["gm-s1"]
    r = row(db["path"])
    assert r["status"] == "suspended"
    assert r["payment_status"] == "expired"
    assert r["suspended_at"]


async def test_sweep_never_touches_a_paid_server(db):
    insert_server(db["path"], payment_status="paid", due_offset_minutes=-999)
    assert await billing.sweep_overdue() == 0
    assert db["calls"]["stopped"] == []


async def test_sweep_is_idempotent(db):
    insert_server(db["path"], due_offset_minutes=-1)
    await billing.sweep_overdue()
    db["calls"]["stopped"].clear()
    assert await billing.sweep_overdue() == 0, "already-suspended server must not be stopped twice"


# --- payment confirmation --------------------------------------------------


async def test_mark_paid_restarts_a_suspended_server(db):
    insert_server(db["path"], status="suspended", payment_status="expired", due_offset_minutes=-5)
    result = await billing.mark_paid("1234")
    assert result["ok"] and result["restarted"]
    assert db["calls"]["started"] == ["gm-s1"]
    r = row(db["path"])
    assert r["payment_status"] == "paid"
    assert r["status"] == "running"
    assert r["paid_at"]


async def test_mark_paid_on_running_server_does_not_restart(db):
    insert_server(db["path"])
    result = await billing.mark_paid("1234")
    assert result["ok"] and not result["restarted"]
    assert db["calls"]["started"] == []


async def test_mark_paid_unknown_code(db):
    insert_server(db["path"])
    result = await billing.mark_paid("9999")
    assert not result["ok"]


async def test_mark_paid_twice_is_safe(db):
    insert_server(db["path"])
    await billing.mark_paid("1234")
    second = await billing.mark_paid("1234")
    assert second["ok"] and second.get("already_paid")


# --- the customer's one-time extension -------------------------------------


async def test_extension_pushes_the_deadline_out(db):
    insert_server(db["path"], due_offset_minutes=1)
    before = billing.parse_iso(row(db["path"])["payment_due_at"])
    result = await billing.request_extension("tok1")
    assert result["ok"]
    after = billing.parse_iso(row(db["path"])["payment_due_at"])
    assert after > before
    assert row(db["path"])["grace_extended"] == 1


async def test_extension_revives_an_already_suspended_server(db):
    insert_server(db["path"], status="suspended", payment_status="expired", due_offset_minutes=-5)
    result = await billing.request_extension("tok1")
    assert result["ok"]
    assert db["calls"]["started"] == ["gm-s1"]
    r = row(db["path"])
    assert r["status"] == "running"
    assert r["payment_status"] == "awaiting"


async def test_extension_is_granted_only_once(db):
    insert_server(db["path"])
    assert (await billing.request_extension("tok1"))["ok"]
    second = await billing.request_extension("tok1")
    assert not second["ok"]


async def test_extension_on_paid_server_is_a_noop(db):
    insert_server(db["path"], payment_status="paid")
    result = await billing.request_extension("tok1")
    assert result["ok"]
    assert row(db["path"])["grace_extended"] == 0


# --- codes, prices, helpers ------------------------------------------------


def test_payment_code_avoids_codes_in_use(db, monkeypatch):
    insert_server(db["path"], code="0001")
    conn = connect(db["path"])
    try:
        monkeypatch.setattr(billing.secrets, "randbelow", lambda n: 1)
        with pytest.raises(RuntimeError):
            billing.generate_payment_code(conn)
    finally:
        conn.close()


def test_payment_code_is_four_digits(db):
    conn = connect(db["path"])
    try:
        code = billing.generate_payment_code(conn)
        assert len(code) == 4 and code.isdigit()
    finally:
        conn.close()


def test_prices_match_the_landing_page():
    assert billing.TIER_PRICES == {"small": 4900, "friends": 11900, "community": 49000}


def test_seconds_remaining_is_zero_not_negative_when_overdue(db):
    insert_server(db["path"], due_offset_minutes=-10)
    assert billing.seconds_remaining(row(db["path"])) == 0


def test_seconds_remaining_is_none_once_paid(db):
    insert_server(db["path"], payment_status="paid")
    assert billing.seconds_remaining(row(db["path"])) is None
