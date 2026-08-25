"""Customer accounts.

Two properties carry real security weight and get the most attention here:
passwords are never recoverable from what is stored, and claiming a server
cannot be used to steal someone else's.
"""

import pytest

from app import accounts
from app.accounts import AccountError
from tests.conftest import connect, make_test_db


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    path = make_test_db(str(tmp_path / "accounts.db"))
    monkeypatch.setattr(accounts, "get_connection", lambda: connect(path))
    # Real scrypt parameters are deliberately slow; a lower cost keeps the
    # suite fast while exercising the identical code path.
    monkeypatch.setattr(accounts, "SCRYPT_N", 2 ** 8)
    return path


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


# --- production KDF parameters ---------------------------------------------


def test_real_scrypt_parameters_actually_work(monkeypatch):
    """Deliberately NOT using the fixture's reduced cost.

    Every other test lowers SCRYPT_N so the suite stays fast, which meant the
    real parameters were never executed — and they failed in production:
    N=2^15, r=8 needs exactly 32MiB, which is OpenSSL's default ceiling, so
    every registration died with "memory limit exceeded". This test is the
    one that would have caught it.
    """
    monkeypatch.undo()
    stored = accounts.hash_password("goodpassword123")
    assert stored.startswith(f"scrypt${2 ** 15}$8$1$")
    assert accounts.verify_password("goodpassword123", stored)
    assert not accounts.verify_password("wrongpassword", stored)


def test_maxmem_covers_the_real_requirement():
    # scrypt needs 128*N*r*p bytes; anything less is rejected outright.
    assert accounts._maxmem_for(2 ** 15, 8, 1) >= 128 * (2 ** 15) * 8 * 1


# --- password handling -----------------------------------------------------


def test_password_is_not_stored_in_plaintext():
    stored = accounts.hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in stored
    assert stored.startswith("scrypt$")


def test_same_password_hashes_differently_each_time():
    """Distinct salts — otherwise identical passwords are visibly identical
    in the database."""
    assert accounts.hash_password("hunter2222") != accounts.hash_password("hunter2222")


def test_verify_accepts_the_right_password_and_rejects_others():
    stored = accounts.hash_password("hunter2222")
    assert accounts.verify_password("hunter2222", stored)
    assert not accounts.verify_password("hunter2223", stored)
    assert not accounts.verify_password("", stored)


def test_verify_on_a_corrupt_hash_returns_false_rather_than_raising():
    for bad in ("", "garbage", "scrypt$nope", "md5$1$2$3$4$5"):
        assert accounts.verify_password("x", bad) is False


def test_short_password_rejected():
    with pytest.raises(AccountError, match="8자"):
        accounts.register("a@b.com", "short")


def test_absurdly_long_password_rejected():
    """scrypt on unbounded input is a free CPU-exhaustion vector."""
    with pytest.raises(AccountError):
        accounts.register("a@b.com", "x" * 5000)


@pytest.mark.parametrize("email", ["notanemail", "a@b", "@b.com", "a b@c.com", ""])
def test_bad_emails_rejected(email):
    with pytest.raises(AccountError):
        accounts.register(email, "goodpassword")


# --- register / login ------------------------------------------------------


def test_register_then_login():
    accounts.register("Steve@Example.com ", "goodpassword")
    result = accounts.login("steve@example.com", "goodpassword")
    assert result["token"]
    assert result["email"] == "steve@example.com"


def test_email_is_case_insensitive_and_trimmed():
    accounts.register("  Steve@Example.COM  ", "goodpassword")
    with pytest.raises(AccountError, match="이미 가입"):
        accounts.register("steve@example.com", "goodpassword")


def test_login_with_wrong_password_fails():
    accounts.register("a@b.com", "goodpassword")
    with pytest.raises(AccountError):
        accounts.login("a@b.com", "wrongpassword")


def test_login_for_unknown_email_gives_the_same_error(db):
    """Must not reveal whether an address is registered."""
    accounts.register("a@b.com", "goodpassword")
    with pytest.raises(AccountError) as wrong_pw:
        accounts.login("a@b.com", "wrongpassword")
    with pytest.raises(AccountError) as no_user:
        accounts.login("nobody@b.com", "wrongpassword")
    assert str(wrong_pw.value) == str(no_user.value)


def test_token_resolves_to_the_user():
    token = accounts.register("a@b.com", "goodpassword")["token"]
    assert accounts.user_for_token(token)["email"] == "a@b.com"


def test_bad_or_missing_token_resolves_to_nobody():
    for bad in (None, "", "not-a-real-token"):
        assert accounts.user_for_token(bad) is None


def test_logout_invalidates_the_token():
    token = accounts.register("a@b.com", "goodpassword")["token"]
    accounts.logout(token)
    assert accounts.user_for_token(token) is None


def test_expired_token_is_rejected(db):
    token = accounts.register("a@b.com", "goodpassword")["token"]
    conn = connect(db)
    conn.execute("UPDATE auth_tokens SET expires_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    assert accounts.user_for_token(token) is None


# --- claiming a server -----------------------------------------------------


def test_registering_claims_the_server_you_are_looking_at(db):
    make_server(db, token="tok1")
    result = accounts.register("a@b.com", "goodpassword", claim_session_token="tok1")
    assert result["claimed_server"] is True

    user = accounts.user_for_token(result["token"])
    assert [s["nickname"] for s in accounts.servers_for_user(user["id"])] == ["Steve"]


def test_login_can_also_claim_an_unclaimed_server(db):
    accounts.register("a@b.com", "goodpassword")
    make_server(db, token="tok1")
    assert accounts.login("a@b.com", "goodpassword", claim_session_token="tok1")["claimed_server"]


def test_cannot_steal_a_server_already_owned_by_someone_else(db):
    """The session token travels in a URL. If holding one were enough to
    re-bind an already-claimed server, sharing a link would hand over the
    account — worse than the exposure accounts are meant to fix."""
    make_server(db, token="tok1")
    first = accounts.register("owner@b.com", "goodpassword", claim_session_token="tok1")
    assert first["claimed_server"] is True

    attacker = accounts.register("thief@b.com", "goodpassword", claim_session_token="tok1")
    assert attacker["claimed_server"] is False

    owner = accounts.user_for_token(first["token"])
    thief = accounts.user_for_token(attacker["token"])
    assert len(accounts.servers_for_user(owner["id"])) == 1
    assert accounts.servers_for_user(thief["id"]) == []


def test_unknown_session_token_claims_nothing(db):
    result = accounts.register("a@b.com", "goodpassword", claim_session_token="does-not-exist")
    assert result["claimed_server"] is False


def test_registering_without_a_session_token_is_fine():
    assert accounts.register("a@b.com", "goodpassword")["claimed_server"] is False


def test_users_only_see_their_own_servers(db):
    make_server(db, sid="s1", token="tok1", nickname="Alice")
    make_server(db, sid="s2", token="tok2", nickname="Bob")
    a = accounts.register("a@b.com", "goodpassword", claim_session_token="tok1")
    b = accounts.register("b@b.com", "goodpassword", claim_session_token="tok2")

    ua = accounts.user_for_token(a["token"])
    ub = accounts.user_for_token(b["token"])
    assert [s["nickname"] for s in accounts.servers_for_user(ua["id"])] == ["Alice"]
    assert [s["nickname"] for s in accounts.servers_for_user(ub["id"])] == ["Bob"]
    assert accounts.user_owns_session(ua["id"], "tok1")
    assert not accounts.user_owns_session(ua["id"], "tok2")
