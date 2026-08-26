"""The address a player types into Minecraft.

Each customer server gets its own subdomain under MINECRAFT_DOMAIN, resolved
by one wildcard A record. The port stays in the address: an A record maps a
name to an address and every server shares one, so removing the port would
take an SRV record created per server.

Slugs are handed out once and never reassigned — customers give this address
to their friends, so a recycled slug would silently point someone at a
stranger's server.
"""

import sqlite3

import pytest

from app import naming
from app.config import settings
from tests.conftest import connect, make_test_db


def make_row(path, *, sid="s1", slug="k7m2xq", port=25567):
    conn = connect(path)
    conn.execute(
        "INSERT INTO applications (id, tier, status, created_at) "
        "VALUES (?, 'small', 'provisioned', '2026-01-01')",
        (f"app-{sid}",),
    )
    conn.execute(
        """INSERT INTO servers
           (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
            container_name, game_port, rcon_password, status, created_at, mc_slug)
           VALUES (?, ?, '', 'small', 2048, 15, '26.2', 'PAPER',
                   ?, ?, 'pw', 'running', '2026-01-01', ?)""",
        (sid, f"app-{sid}", f"gm-{sid}", port, slug),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return row


@pytest.fixture
def db(tmp_path):
    return make_test_db(str(tmp_path / "addr.db"))


# --- rendering -------------------------------------------------------------


def test_the_address_is_the_servers_own_subdomain(db, monkeypatch):
    monkeypatch.setattr(settings, "minecraft_domain", "mc.glisteringmelon.org")
    assert naming.server_address(make_row(db)) == "k7m2xq.mc.glisteringmelon.org:25567"


def test_no_domain_configured_falls_back_to_the_bare_host(db, monkeypatch):
    """Development runs on the LAN with no domain at all."""
    monkeypatch.setattr(settings, "minecraft_domain", "")
    monkeypatch.setattr(settings, "minecraft_public_host", "192.168.45.100")
    assert naming.server_address(make_row(db)) == "192.168.45.100:25567"


def test_a_server_from_before_slugs_still_gets_an_address(db, monkeypatch):
    """Existing rows have no slug; they must not render "None.mc...."."""
    monkeypatch.setattr(settings, "minecraft_domain", "mc.glisteringmelon.org")
    assert naming.server_address(make_row(db, slug=None)) == "mc.glisteringmelon.org:25567"


def test_a_stray_dot_in_the_domain_is_tolerated(db, monkeypatch):
    monkeypatch.setattr(settings, "minecraft_domain", ".mc.glisteringmelon.org.")
    assert naming.server_address(make_row(db)) == "k7m2xq.mc.glisteringmelon.org:25567"


def test_the_ip_never_appears_once_a_domain_is_set(db, monkeypatch):
    monkeypatch.setattr(settings, "minecraft_domain", "mc.glisteringmelon.org")
    monkeypatch.setattr(settings, "minecraft_public_host", "192.168.45.100")
    assert "192.168" not in naming.server_address(make_row(db))


# --- slugs -----------------------------------------------------------------


def test_a_slug_is_a_valid_hostname_label(db):
    conn = connect(db)
    slug = naming.generate_slug(conn)
    conn.close()
    assert 1 <= len(slug) <= 63
    assert slug.isascii() and slug.islower()
    assert all(c.isalnum() for c in slug)


def test_slugs_avoid_characters_that_get_misread():
    """This gets typed in by a friend, often off a screenshot."""
    for confusable in "01oil":
        assert confusable not in naming.SLUG_ALPHABET


def test_a_slug_in_use_is_never_handed_out_again(db, monkeypatch):
    """A recycled slug would point someone at a stranger's server."""
    make_row(db, sid="s1", slug="k7m2xq")
    monkeypatch.setattr(naming.secrets, "choice", lambda alphabet: "k")

    conn = connect(db)
    with pytest.raises(RuntimeError, match="could not allocate"):
        # Every attempt produces "kkkkkk"; once that is taken there is nowhere
        # left to go, and giving up beats colliding.
        conn.execute("UPDATE servers SET mc_slug = 'kkkkkk' WHERE id = 's1'")
        conn.commit()
        naming.generate_slug(conn)
    conn.close()


def test_two_servers_cannot_share_a_slug(db):
    make_row(db, sid="s1", slug="k7m2xq", port=25565)
    with pytest.raises(sqlite3.IntegrityError):
        make_row(db, sid="s2", slug="k7m2xq", port=25566)


def test_slugs_are_unique_across_a_batch(db):
    conn = connect(db)
    seen = set()
    for index in range(50):
        slug = naming.generate_slug(conn)
        assert slug not in seen
        seen.add(slug)
        conn.execute(
            "INSERT INTO applications (id, tier, status, created_at) "
            "VALUES (?, 'small', 'received', '2026-01-01')",
            (f"a{index}",),
        )
        conn.execute(
            """INSERT INTO servers
               (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
                container_name, game_port, rcon_password, status, created_at, mc_slug)
               VALUES (?, ?, '', 'small', 2048, 15, '26.2', 'PAPER', ?, ?, 'pw',
                       'running', '2026-01-01', ?)""",
            (f"s{index}", f"a{index}", f"gm-{index}", 26000 + index, slug),
        )
    conn.commit()
    conn.close()


# --- CORS ------------------------------------------------------------------


def test_a_single_origin_still_works(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origin", "https://glisteringmelon.org")
    assert settings.cors_origins == ["https://glisteringmelon.org"]


def test_several_origins_are_split(monkeypatch):
    """The landing page is on Cloudflare Pages and the API is not, so more
    than one origin legitimately calls this backend."""
    monkeypatch.setattr(
        settings,
        "cors_allow_origin",
        "https://glisteringmelon.org, https://www.glisteringmelon.org",
    )
    assert settings.cors_origins == [
        "https://glisteringmelon.org",
        "https://www.glisteringmelon.org",
    ]


def test_the_wildcard_survives_as_one_entry(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origin", "*")
    assert settings.cors_origins == ["*"]


def test_blank_entries_are_dropped(monkeypatch):
    """A trailing comma in .env must not become an origin that matches nothing
    — or worse, an empty string the middleware treats oddly."""
    monkeypatch.setattr(settings, "cors_allow_origin", "https://a.org,,https://b.org,")
    assert settings.cors_origins == ["https://a.org", "https://b.org"]
