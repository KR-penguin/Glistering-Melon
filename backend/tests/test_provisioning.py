import pytest

from app import provisioning
from tests.conftest import connect as _connect
from tests.conftest import make_test_db


@pytest.fixture(autouse=True)
def patched(tmp_path, monkeypatch):
    db_path = make_test_db(str(tmp_path / "test.db"))

    # Real behavior: get_connection() opens a fresh connection to the same
    # file each call and submit_application closes it when done — mirror
    # that here instead of sharing one connection (which submit_application
    # would then close out from under later assertions).
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
    for name in (
        "send_no_capacity_email",
        "send_provisioned_email",
        "send_provision_failed_email",
    ):
        monkeypatch.setattr(provisioning.email_notify, name, noop)

    async def noop_background(server_id):
        return None

    monkeypatch.setattr(provisioning, "_provision_background", noop_background)

    return db_path


async def test_provisions_when_capacity_ok(patched):
    result = await provisioning.submit_application("Steve", "steve@example.com", "small", None)

    assert result["status"] == "provisioning"
    conn = _connect(patched)
    app_row = conn.execute(
        "SELECT status FROM applications WHERE id=?", (result["application_id"],)
    ).fetchone()
    assert app_row["status"] == "provisioning"
    server_row = conn.execute(
        "SELECT tier, ram_mb, modpack_key FROM servers WHERE id=?", (result["server_id"],)
    ).fetchone()
    assert server_row["tier"] == "small"
    assert server_row["ram_mb"] == 2048
    assert server_row["modpack_key"] is None
    conn.close()


async def test_no_capacity_branch(patched, monkeypatch):
    monkeypatch.setattr(provisioning.settings, "host_ram_mb_budget", 100)

    result = await provisioning.submit_application("Alex", "alex@example.com", "small", None)

    assert result["status"] == "no_capacity"
    conn = _connect(patched)
    app_row = conn.execute(
        "SELECT status FROM applications WHERE id=?", (result["application_id"],)
    ).fetchone()
    assert app_row["status"] == "no_capacity"
    assert conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0] == 0
    conn.close()


async def test_second_application_sees_first_reservation(patched, monkeypatch):
    # Budget = exactly one "friends" tier (6144MB), so a second "small"
    # (2048MB) application must see the first's reservation and be blocked.
    monkeypatch.setattr(provisioning.settings, "host_ram_mb_budget", 6144)

    first = await provisioning.submit_application("Steve", "steve@example.com", "friends", None)
    assert first["status"] == "provisioning"

    second = await provisioning.submit_application("Alex", "alex@example.com", "small", None)
    assert second["status"] == "no_capacity"


async def test_modpack_key_is_persisted(patched):
    result = await provisioning.submit_application(
        "Steve", "steve@example.com", "friends", "cobblemon"
    )
    conn = _connect(patched)
    server_row = conn.execute(
        "SELECT modpack_key FROM servers WHERE id=?", (result["server_id"],)
    ).fetchone()
    assert server_row["modpack_key"] == "cobblemon"
    conn.close()
