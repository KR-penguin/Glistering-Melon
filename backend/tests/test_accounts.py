"""Customer accounts, now that identity comes from Google.

The property carrying real security weight is unchanged and still gets the
most attention here: claiming a server cannot be used to steal someone else's.
What went away is the password surface — no hashing, no login attempts, no
recoverable credential in the database, because this module never sees one.
"""

import pytest

from app import accounts
from tests.conftest import connect, make_test_db


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    path = make_test_db(str(tmp_path / "accounts.db"))
    monkeypatch.setattr(accounts, "get_connection", lambda: connect(path))
    return path


def sign_in(email="a@b.com", sub=None, name=None, claim=None):
    """Shorthand for the one way in."""
    return accounts.sign_in_with_google(
        google_sub=sub or f"sub-{email}",
        email=email,
        display_name=name,
        claim_session_token=claim,
    )


def make_server(path, *, sid="s1", token="tok1", user_id=None, nickname="Steve"):
    conn = connect(path)
    conn.execute(
        "INSERT INTO applications (id, nickname, email, tier, status, created_at) "
        "VALUES (?, ?, 'a@b.com', 'small', 'provisioned', '2026-01-01')",
        (f"app-{sid}", nickname),
    )
    conn.execute(
        """INSERT INTO servers
           (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
            container_name, game_port, rcon_password, status, created_at,
            session_token, price_krw, payment_status, payment_code, grace_extended, user_id)
           VALUES (?, ?, ?, 'small', 2048, 15, '1.21.1', 'PAPER',
                   ?, 25565, 'pw', 'running', '2026-01-01', ?, 4900, 'awaiting', '1111', 0, ?)""",
        (sid, f"app-{sid}", nickname, f"gm-{sid}", token, user_id),
    )
    conn.commit()
    conn.close()


# --- identity --------------------------------------------------------------


def test_first_sign_in_creates_the_account():
    result = sign_in("a@b.com")
    assert accounts.user_for_token(result["token"])["email"] == "a@b.com"


def test_signing_in_again_reuses_the_same_account(db):
    first = sign_in("a@b.com", sub="sub-1")
    second = sign_in("a@b.com", sub="sub-1")
    assert first["token"] != second["token"]

    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_the_subject_id_wins_over_the_address(db):
    """Google addresses can change; the subject id does not. A changed
    address must not fork the account and orphan their servers."""
    sign_in("old@b.com", sub="sub-1")
    sign_in("new@b.com", sub="sub-1")

    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT email FROM users").fetchone()[0] == "new@b.com"
    conn.close()


def test_a_changed_address_does_not_displace_another_account(db):
    """users.email is UNIQUE. If the new address already belongs to someone
    else, keeping the stale one beats merging two customers' servers."""
    sign_in("taken@b.com", sub="sub-other")
    sign_in("mine@b.com", sub="sub-1")
    sign_in("taken@b.com", sub="sub-1")

    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    emails = {r[0] for r in conn.execute("SELECT email FROM users")}
    assert emails == {"taken@b.com", "mine@b.com"}
    conn.close()


def test_two_different_people_get_two_accounts(db):
    sign_in("a@b.com", sub="sub-1")
    sign_in("b@b.com", sub="sub-2")
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    conn.close()


def test_an_account_from_the_password_era_is_adopted(db):
    """users.email is UNIQUE, so a pre-existing row has to be linked rather
    than inserted alongside — otherwise the customer's servers are stranded
    behind a login that no longer exists."""
    conn = connect(db)
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES ('legacy', 'a@b.com', 'scrypt$old', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    result = sign_in("a@b.com", sub="sub-1")
    assert accounts.user_for_token(result["token"])["id"] == "legacy"

    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT google_sub FROM users").fetchone()[0] == "sub-1"
    conn.close()


def test_email_is_case_insensitive_and_trimmed(db):
    sign_in("  A@B.com ", sub="sub-1")
    conn = connect(db)
    assert conn.execute("SELECT email FROM users").fetchone()[0] == "a@b.com"
    conn.close()


# --- tokens ----------------------------------------------------------------


def test_token_resolves_to_the_user():
    assert accounts.user_for_token(sign_in()["token"])["email"] == "a@b.com"


def test_bad_or_missing_token_resolves_to_nobody():
    for bad in (None, "", "not-a-real-token"):
        assert accounts.user_for_token(bad) is None


def test_logout_invalidates_the_token():
    token = sign_in()["token"]
    accounts.logout(token)
    assert accounts.user_for_token(token) is None


def test_expired_token_is_rejected(db):
    token = sign_in()["token"]
    conn = connect(db)
    conn.execute("UPDATE auth_tokens SET expires_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    assert accounts.user_for_token(token) is None


# --- claiming a server -----------------------------------------------------


def test_signing_in_claims_the_server_you_are_looking_at(db):
    make_server(db, token="tok1")
    result = sign_in(claim="tok1")
    assert result["claimed_server"] is True

    user = accounts.user_for_token(result["token"])
    assert [s["nickname"] for s in accounts.servers_for_user(user["id"])] == ["Steve"]


def test_a_returning_customer_can_also_claim(db):
    sign_in("a@b.com", sub="sub-1")
    make_server(db, token="tok1")
    assert sign_in("a@b.com", sub="sub-1", claim="tok1")["claimed_server"]


def test_cannot_steal_a_server_already_owned_by_someone_else(db):
    """The session token travels in a URL. If holding one were enough to
    re-bind an already-claimed server, sharing a link would hand over the
    account — worse than the exposure accounts are meant to fix."""
    make_server(db, token="tok1")
    first = sign_in("owner@b.com", sub="sub-owner", claim="tok1")
    assert first["claimed_server"] is True

    attacker = sign_in("thief@b.com", sub="sub-thief", claim="tok1")
    assert attacker["claimed_server"] is False

    owner = accounts.user_for_token(first["token"])
    thief = accounts.user_for_token(attacker["token"])
    assert len(accounts.servers_for_user(owner["id"])) == 1
    assert accounts.servers_for_user(thief["id"]) == []


def test_unknown_session_token_claims_nothing():
    assert sign_in(claim="does-not-exist")["claimed_server"] is False


def test_signing_in_without_a_session_token_is_fine():
    assert sign_in()["claimed_server"] is False


def test_users_only_see_their_own_servers(db):
    make_server(db, sid="s1", token="tok1", nickname="Alice")
    make_server(db, sid="s2", token="tok2", nickname="Bob")
    a = sign_in("a@b.com", sub="sub-a", claim="tok1")
    b = sign_in("b@b.com", sub="sub-b", claim="tok2")

    ua = accounts.user_for_token(a["token"])
    ub = accounts.user_for_token(b["token"])
    assert [s["nickname"] for s in accounts.servers_for_user(ua["id"])] == ["Alice"]
    assert [s["nickname"] for s in accounts.servers_for_user(ub["id"])] == ["Bob"]
    assert accounts.user_owns_session(ua["id"], "tok1")
    assert not accounts.user_owns_session(ua["id"], "tok2")


# --- naming a server that was opened anonymously ---------------------------


def test_google_name_fills_in_a_nameless_server(db):
    """The server opens with no name at all; this is where one arrives."""
    make_server(db, token="tok1", nickname="")
    sign_in(name="김철수", claim="tok1")

    conn = connect(db)
    assert conn.execute("SELECT nickname FROM servers").fetchone()[0] == "김철수"
    conn.close()


def test_a_name_the_customer_chose_is_not_overwritten(db):
    make_server(db, token="tok1", nickname="내 서버")
    sign_in(name="김철수", claim="tok1")

    conn = connect(db)
    assert conn.execute("SELECT nickname FROM servers").fetchone()[0] == "내 서버"
    conn.close()


def test_an_overlong_google_name_is_truncated(db):
    """nickname is rendered inline in Korean sentences and in /admin; the
    form used to cap it at 20 and Google's profile name has no such limit."""
    make_server(db, token="tok1", nickname="")
    sign_in(name="가" * 60, claim="tok1")

    conn = connect(db)
    assert len(conn.execute("SELECT nickname FROM servers").fetchone()[0]) == 20
    conn.close()


def test_an_unclaimed_server_is_not_renamed(db):
    """No claim, no rename — otherwise holding a session URL would let anyone
    relabel someone else's server."""
    make_server(db, token="tok1", nickname="", user_id="someone-else")
    sign_in(name="김철수", claim="tok1")

    conn = connect(db)
    assert conn.execute("SELECT nickname FROM servers").fetchone()[0] == ""
    conn.close()
