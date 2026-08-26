"""Opening a server asks for nothing.

The landing page used to collect a name, an email and a plan before it would
call POST /api/servers. Now the button IS the signup, so the request can
legitimately arrive with an empty body — and applications.email was NOT NULL,
which turned exactly that request into a 500 rather than a validation error.
"""

import pytest

from app import provisioning
from app.routes.servers import DEFAULT_TIER
from tests.conftest import connect as _connect
from tests.conftest import make_test_db


@pytest.fixture(autouse=True)
def patched(tmp_path, monkeypatch):
    db_path = make_test_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(provisioning, "get_connection", lambda: _connect(db_path))
    monkeypatch.setattr(provisioning.docker_ctl, "allocate_port", lambda conn: 25565)
    monkeypatch.setattr(provisioning.settings, "host_ram_mb_budget", 8192)
    monkeypatch.setattr(provisioning.settings, "host_storage_gb_budget", 200)
    monkeypatch.setattr(provisioning.settings, "max_concurrent_servers", 5)

    async def noop(*args, **kwargs):
        return None

    for name in (
        "notify_new_application",
        "notify_capacity_alert",
        "notify_provisioned",
        "notify_provision_failed",
    ):
        monkeypatch.setattr(provisioning.discord_notify, name, noop)

    async def noop_background(server_id):
        return None

    monkeypatch.setattr(provisioning, "_provision_background", noop_background)
    return db_path


async def test_a_server_opens_with_no_name_and_no_email(patched):
    """The regression: this raised sqlite3.IntegrityError and 500'd."""
    result = await provisioning.submit_application(None, None, "small", None)

    assert result["status"] == "provisioning"
    conn = _connect(patched)
    app_row = conn.execute("SELECT * FROM applications").fetchone()
    assert app_row["nickname"] is None and app_row["email"] is None


async def test_the_server_row_records_no_name(patched):
    await provisioning.submit_application(None, None, "small", None)
    conn = _connect(patched)
    row = conn.execute("SELECT * FROM servers").fetchone()
    # NOT NULL on servers.nickname, so absent is stored as empty, not null.
    assert row["nickname"] == ""
    assert row["email"] is None
    assert row["session_token"] and row["payment_code"]


async def test_a_name_is_still_recorded_when_one_is_given(patched):
    await provisioning.submit_application("김철수", "a@b.com", "small", None)
    conn = _connect(patched)
    assert conn.execute("SELECT nickname FROM servers").fetchone()["nickname"] == "김철수"


# --- the HTTP surface ------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    """Every TestClient request comes from the same IP, and creation is capped
    at 3 per 10 minutes — without this the fourth test in the file silently
    asserts against a 429 instead of the route."""
    from app import ratelimit

    ratelimit._hits.clear()
    yield
    ratelimit._hits.clear()


@pytest.fixture
def stub_create(monkeypatch):
    """Capture what the route hands provisioning, without provisioning."""
    captured = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return {
            "status": "provisioning",
            "application_id": "a",
            "server_id": "s",
            "session_token": "tok",
            "session_url": "http://x/session/tok",
        }

    from app.routes import servers as servers_route

    monkeypatch.setattr(servers_route.provisioning, "submit_application", fake_submit)
    return captured


def test_an_empty_body_opens_a_server(app_client, stub_create):
    response = app_client.post("/api/servers", json={})
    assert response.status_code == 200, response.text
    assert response.json()["session_token"] == "tok"


def test_an_omitted_plan_falls_back_to_the_cheapest(app_client, stub_create):
    """One click with no form must never commit someone to the 49,000원 tier."""
    app_client.post("/api/servers", json={})
    assert stub_create["tier"] == DEFAULT_TIER == "small"


def test_a_named_plan_is_honoured(app_client, stub_create):
    app_client.post("/api/servers", json={"tier": "community"})
    assert stub_create["tier"] == "community"


def test_an_unknown_plan_is_still_rejected(app_client, stub_create):
    assert app_client.post("/api/servers", json={"tier": "enterprise"}).status_code == 422


def test_no_name_means_no_operator(app_client, stub_create):
    """Never let an unverified name become OPS — and with no name there is
    nothing to look up, so Mojang must not be consulted at all."""
    app_client.post("/api/servers", json={})
    assert stub_create["mc_username"] is None


def test_a_blank_name_is_treated_as_absent(app_client, stub_create):
    app_client.post("/api/servers", json={"nickname": "   "})
    assert stub_create["nickname"] is None


def test_an_overlong_name_is_still_rejected(app_client, stub_create):
    assert app_client.post("/api/servers", json={"nickname": "가" * 21}).status_code == 422


def test_a_malformed_email_is_still_rejected(app_client, stub_create):
    assert app_client.post("/api/servers", json={"email": "not-an-email"}).status_code == 422
