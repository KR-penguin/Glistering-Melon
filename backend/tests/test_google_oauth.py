"""Signing in with Google.

The callback is the moment a session token gets bound to an account, which
makes CSRF here equivalent to account takeover: feed a signed-in customer a
crafted callback URL and their server becomes yours. So `state` gets most of
the attention — generated per attempt, single-use, and expiring.
"""

import pytest

from app import accounts, google_oauth
from app.config import settings
from app.google_oauth import OAuthError
from tests.conftest import connect, make_test_db


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    monkeypatch.setattr(settings, "oauth_redirect_base", "http://localhost:8000")
    monkeypatch.setattr(settings, "public_base_url", "http://192.168.45.100:8000")
    google_oauth._pending.clear()
    yield
    google_oauth._pending.clear()


# --- the outbound leg ------------------------------------------------------


def test_the_authorize_url_carries_what_google_needs():
    url = google_oauth.begin(None)
    assert url.startswith(google_oauth.AUTH_ENDPOINT)
    for expected in ("client_id=", "response_type=code", "state=", "prompt=select_account"):
        assert expected in url


def test_the_redirect_uri_prefers_the_oauth_base():
    """Google rejects a LAN address at registration time, so the redirect
    cannot simply follow public_base_url."""
    assert google_oauth.redirect_uri() == "http://localhost:8000/api/auth/google/callback"


def test_the_redirect_uri_falls_back_to_the_public_url(monkeypatch):
    monkeypatch.setattr(settings, "oauth_redirect_base", "")
    assert google_oauth.redirect_uri() == "http://192.168.45.100:8000/api/auth/google/callback"


def test_starting_without_credentials_is_a_readable_error(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    with pytest.raises(OAuthError, match="설정되지 않았"):
        google_oauth.begin(None)


def test_each_attempt_gets_its_own_state():
    first = google_oauth.begin(None)
    second = google_oauth.begin(None)
    assert first != second
    assert len(google_oauth._pending) == 2


# --- state, i.e. the CSRF boundary -----------------------------------------


def _state_from(url: str) -> str:
    return url.split("state=")[1].split("&")[0]


def test_state_round_trips_the_session_token():
    state = _state_from(google_oauth.begin("tok-abc"))
    assert google_oauth.consume_state(state) == "tok-abc"


def test_state_is_single_use():
    """A state coming back twice is a replay, not a retry."""
    state = _state_from(google_oauth.begin("tok-abc"))
    google_oauth.consume_state(state)
    with pytest.raises(OAuthError, match="만료"):
        google_oauth.consume_state(state)


def test_an_unknown_state_is_refused():
    with pytest.raises(OAuthError):
        google_oauth.consume_state("never-issued")


def test_a_missing_state_is_refused():
    for empty in (None, ""):
        with pytest.raises(OAuthError):
            google_oauth.consume_state(empty)


def test_an_expired_state_is_refused(monkeypatch):
    # The clock has to be under control before the state is stamped, or the
    # comparison runs against a real monotonic reading and never expires.
    ticks = [1000.0]
    monkeypatch.setattr(google_oauth.time, "monotonic", lambda: ticks[0])

    state = _state_from(google_oauth.begin("tok-abc"))
    ticks[0] += google_oauth.STATE_TTL_SECONDS + 1

    with pytest.raises(OAuthError, match="만료"):
        google_oauth.consume_state(state)


def test_a_state_still_inside_its_window_is_accepted(monkeypatch):
    ticks = [1000.0]
    monkeypatch.setattr(google_oauth.time, "monotonic", lambda: ticks[0])

    state = _state_from(google_oauth.begin("tok-abc"))
    ticks[0] += google_oauth.STATE_TTL_SECONDS - 1

    assert google_oauth.consume_state(state) == "tok-abc"


# --- the identity we accept ------------------------------------------------


def _claims(**overrides):
    base = {
        "sub": "google-sub-1",
        "email": "a@b.com",
        "email_verified": True,
        "name": "김철수",
    }
    base.update(overrides)
    return base


async def test_a_verified_identity_is_accepted(monkeypatch):
    monkeypatch.setattr(google_oauth, "_verify_id_token_sync", lambda t: _claims())
    identity = await google_oauth.verify_id_token("raw")
    assert identity == {"sub": "google-sub-1", "email": "a@b.com", "name": "김철수"}


async def test_an_unverified_address_is_rejected(monkeypatch):
    """Otherwise someone registers the same address elsewhere and walks in."""
    monkeypatch.setattr(
        google_oauth, "_verify_id_token_sync", lambda t: _claims(email_verified=False)
    )
    with pytest.raises(OAuthError, match="이메일 인증"):
        await google_oauth.verify_id_token("raw")


async def test_an_identity_with_no_email_is_rejected(monkeypatch):
    monkeypatch.setattr(google_oauth, "_verify_id_token_sync", lambda t: _claims(email=None))
    with pytest.raises(OAuthError):
        await google_oauth.verify_id_token("raw")


async def test_a_bad_signature_is_rejected(monkeypatch):
    def boom(token):
        raise ValueError("Token signature invalid")

    monkeypatch.setattr(google_oauth, "_verify_id_token_sync", boom)
    with pytest.raises(OAuthError, match="확인하지 못했"):
        await google_oauth.verify_id_token("raw")


async def test_a_missing_name_is_not_fatal(monkeypatch):
    """profile scope can come back without one; the server just stays unnamed."""
    monkeypatch.setattr(google_oauth, "_verify_id_token_sync", lambda t: _claims(name=""))
    assert (await google_oauth.verify_id_token("raw"))["name"] is None


# --- the callback route ----------------------------------------------------


@pytest.fixture
def signed_in(monkeypatch, tmp_path):
    """Google's half of the exchange, stubbed."""
    path = make_test_db(str(tmp_path / "oauth.db"))
    monkeypatch.setattr(accounts, "get_connection", lambda: connect(path))

    async def fake_exchange(code):
        return {"id_token": "raw-id-token"}

    async def fake_verify(raw):
        return {"sub": "google-sub-1", "email": "a@b.com", "name": "김철수"}

    from app.routes import auth as auth_route

    monkeypatch.setattr(auth_route.google_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth_route.google_oauth, "verify_id_token", fake_verify)
    auth_route._attempts.clear()
    return path


def test_the_start_route_redirects_to_google(app_client, signed_in):
    response = app_client.get("/api/auth/google/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(google_oauth.AUTH_ENDPOINT)


def test_a_callback_without_valid_state_never_signs_anyone_in(app_client, signed_in):
    response = app_client.get(
        "/api/auth/google/callback?code=abc&state=forged", follow_redirects=False
    )
    assert response.status_code == 302
    assert "gm_auth=" not in response.headers["location"]
    assert "gm_error=" in response.headers["location"]

    conn = connect(signed_in)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    conn.close()


def test_pressing_cancel_on_google_is_not_an_error(app_client, signed_in):
    response = app_client.get(
        "/api/auth/google/callback?error=access_denied", follow_redirects=False
    )
    assert response.status_code == 302
    assert "gm_cancelled=1" in response.headers["location"]


def test_a_successful_callback_issues_a_token(app_client, signed_in):
    state = _state_from(google_oauth.begin(None))
    response = app_client.get(
        f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert "#gm_auth=" in location

    token = location.split("#gm_auth=")[1]
    assert accounts.user_for_token(token)["email"] == "a@b.com"


def test_the_token_rides_in_the_fragment_not_the_query(app_client, signed_in):
    """A fragment is never sent to a server and never reaches an access log
    or a Referer header."""
    state = _state_from(google_oauth.begin(None))
    location = app_client.get(
        f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False
    ).headers["location"]
    assert "?gm_auth=" not in location and "&gm_auth=" not in location


def test_signing_in_from_a_session_page_claims_that_server(app_client, signed_in):
    conn = connect(signed_in)
    conn.execute(
        "INSERT INTO applications (id, tier, status, created_at) "
        "VALUES ('app-1', 'small', 'provisioned', '2026-01-01')"
    )
    conn.execute(
        """INSERT INTO servers
           (id, application_id, nickname, tier, ram_mb, storage_gb, mc_version, mc_type,
            container_name, game_port, rcon_password, status, created_at,
            session_token, price_krw, payment_status, payment_code, grace_extended)
           VALUES ('s1', 'app-1', '', 'small', 2048, 15, '26.2', 'PAPER',
                   'gm-s1', 25565, 'pw', 'running', '2026-01-01', 'tok1', 4900,
                   'awaiting', '1111', 0)"""
    )
    conn.commit()
    conn.close()

    state = _state_from(google_oauth.begin("tok1"))
    location = app_client.get(
        f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False
    ).headers["location"]

    # Back to the server they were looking at, not to the login list.
    assert "/session/tok1#gm_auth=" in location

    conn = connect(signed_in)
    row = conn.execute("SELECT nickname, user_id FROM servers WHERE id='s1'").fetchone()
    assert row["user_id"] is not None
    assert row["nickname"] == "김철수"
    conn.close()
