"""Signing in with Google.

The authorization-code flow, server side. The browser never sees the client
secret and never handles a Google token — it comes back holding one of our own
auth tokens, the same kind the password flow used to issue, so everything
downstream of accounts.user_for_token is unchanged.

Two things here are not optional:

* **state** is generated per attempt and checked on the way back. Without it,
  anyone can hand a signed-in customer a crafted callback URL and bind their
  own Google account to that customer's server — the callback is the moment a
  session token gets claimed, so CSRF here is account takeover.
* the **ID token is verified**, not merely decoded. It arrives straight from
  Google's token endpoint over TLS, so this is belt and braces, but the cost
  is one cached certificate fetch and the failure it prevents is impersonation.

Redirect URIs: Google accepts only public https origins and http://localhost.
A LAN address like http://192.168.45.100:8000 is rejected at registration
time, which is why OAUTH_REDIRECT_BASE exists separately from PUBLIC_BASE_URL
— during development it points at localhost through an SSH tunnel while the
rest of the backend keeps answering on the LAN address.
"""

import asyncio
import logging
import secrets
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# openid+email identifies the account; profile is only there to prefill the
# display name on a server that was opened without one.
SCOPES = "openid email profile"

STATE_TTL_SECONDS = 600

# state -> (session_token_to_claim, created_at). One process, one dict — the
# same assumption ratelimit.py makes.
_pending: dict[str, tuple[str | None, float]] = {}


class OAuthError(Exception):
    """Anything the customer should see a readable Korean message for."""


def is_configured() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def redirect_uri() -> str:
    base = settings.oauth_redirect_base or settings.public_base_url
    return f"{base.rstrip('/')}/api/auth/google/callback"


def _prune(now: float) -> None:
    for state, (_token, created) in list(_pending.items()):
        if now - created > STATE_TTL_SECONDS:
            _pending.pop(state, None)


def begin(session_token: str | None) -> str:
    """Return the Google URL to send the browser to."""
    if not is_configured():
        raise OAuthError("구글 로그인이 설정되지 않았습니다. 운영자에게 문의해주세요.")

    now = time.monotonic()
    _prune(now)
    state = secrets.token_urlsafe(24)
    _pending[state] = (session_token, now)

    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # Without this Google silently reuses the previous choice, which on a
        # shared machine signs the last person back in instead of asking.
        "prompt": "select_account",
    }
    return str(httpx.URL(AUTH_ENDPOINT, params=params))


def consume_state(state: str | None) -> str | None:
    """Validate the callback's state and return the session token it carried.

    Single-use: a state that comes back twice is a replay, not a retry.
    """
    if not state:
        raise OAuthError("로그인 요청이 올바르지 않습니다. 다시 시도해주세요.")
    entry = _pending.pop(state, None)
    if entry is None:
        raise OAuthError("로그인 요청이 만료됐습니다. 다시 시도해주세요.")
    session_token, created = entry
    if time.monotonic() - created > STATE_TTL_SECONDS:
        raise OAuthError("로그인 요청이 만료됐습니다. 다시 시도해주세요.")
    return session_token


async def exchange_code(code: str) -> dict:
    """Trade the one-time code for Google's tokens."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError as exc:
        logger.exception("google token exchange failed to connect")
        raise OAuthError("구글에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.") from exc

    if response.status_code != 200:
        # Google puts the reason in the body; the customer gets a generic
        # message but the owner needs the real one in the logs.
        logger.error("google token exchange rejected: %s %s", response.status_code, response.text)
        raise OAuthError("구글 로그인에 실패했습니다. 다시 시도해주세요.")

    payload = response.json()
    if "id_token" not in payload:
        raise OAuthError("구글 로그인에 실패했습니다. 다시 시도해주세요.")
    return payload


def _verify_id_token_sync(raw_id_token: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    return google_id_token.verify_oauth2_token(
        raw_id_token,
        google_requests.Request(),
        settings.google_oauth_client_id,
        # Small allowance: a home server's clock can drift, and a few seconds
        # of skew would otherwise reject every login outright.
        clock_skew_in_seconds=10,
    )


async def verify_id_token(raw_id_token: str) -> dict:
    """Verify the signature and claims, and return the identity.

    Runs off the event loop: the library fetches (and caches) Google's signing
    certificates with a blocking HTTP call.
    """
    try:
        claims = await asyncio.to_thread(_verify_id_token_sync, raw_id_token)
    except Exception as exc:
        logger.exception("google id_token failed verification")
        raise OAuthError("구글 인증 정보를 확인하지 못했습니다.") from exc

    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise OAuthError("구글 계정에서 이메일을 가져오지 못했습니다.")
    if not claims.get("email_verified"):
        # An unverified address would let someone claim an account by
        # registering the same address elsewhere.
        raise OAuthError("이메일 인증이 완료된 구글 계정만 사용할 수 있습니다.")

    return {
        "sub": subject,
        "email": email,
        # Used to fill in the name on a server that was opened without one.
        "name": (claims.get("name") or "").strip() or None,
    }
