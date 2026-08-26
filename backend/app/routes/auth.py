import logging
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app import accounts, google_oauth
from app.config import settings
from app.google_oauth import OAuthError

router = APIRouter()

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Starting a sign-in costs a redirect and, on the way back, a token exchange
# against Google. Cheap, but not free, and unauthenticated.
_WINDOW_SECONDS = 300
_MAX_ATTEMPTS = 20
_attempts: dict[str, list[float]] = {}


def _rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _attempts.get(client_ip, []) if now - t < _WINDOW_SECONDS]
    _attempts[client_ip] = hits
    if len(hits) >= _MAX_ATTEMPTS:
        return True
    hits.append(now)
    return False


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@router.get("/api/auth/google/start")
async def google_start(request: Request, session_token: str | None = None):
    """Send the browser to Google.

    session_token rides along in `state` rather than in the redirect URI:
    Google requires the redirect URI to match the registered one byte for
    byte, so it cannot carry per-request data.
    """
    if _rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="시도가 너무 많습니다. 잠시 후 다시 해주세요.")
    try:
        return RedirectResponse(google_oauth.begin(session_token), status_code=302)
    except OAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _finish_url(fragment: str) -> str:
    """Hand the browser its token in the URL fragment.

    A fragment is never sent to a server and never lands in a Referer header
    or an access log, which a query string would. login.html reads it,
    stores it, and strips it from the address bar.
    """
    return f"{settings.public_base_url.rstrip('/')}/login#{fragment}"


@router.get("/api/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        # The customer pressed cancel on Google's consent screen. Not a fault.
        return RedirectResponse(_finish_url("gm_cancelled=1"), status_code=302)

    try:
        session_token = google_oauth.consume_state(state)
        if not code:
            raise OAuthError("구글 로그인에 실패했습니다. 다시 시도해주세요.")

        tokens = await google_oauth.exchange_code(code)
        identity = await google_oauth.verify_id_token(tokens["id_token"])
    except OAuthError as exc:
        return RedirectResponse(_finish_url(f"gm_error={quote(str(exc))}"), status_code=302)

    result = accounts.sign_in_with_google(
        google_sub=identity["sub"],
        email=identity["email"],
        display_name=identity["name"],
        claim_session_token=session_token,
    )

    # Straight back to the server they were looking at when they signed in.
    if session_token and result["claimed_server"]:
        base = settings.public_base_url.rstrip("/")
        return RedirectResponse(
            f"{base}/session/{session_token}#gm_auth={result['token']}", status_code=302
        )
    return RedirectResponse(_finish_url(f"gm_auth={result['token']}"), status_code=302)


@router.post("/api/auth/logout")
async def logout(x_auth_token: str | None = Header(default=None)):
    accounts.logout(x_auth_token)
    return {"ok": True}


@router.get("/api/auth/me")
async def me(x_auth_token: str | None = Header(default=None)):
    user = accounts.user_for_token(x_auth_token)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return {"email": user["email"], "servers": accounts.servers_for_user(user["id"])}
