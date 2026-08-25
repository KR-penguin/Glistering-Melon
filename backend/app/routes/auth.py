import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import accounts
from app.accounts import AccountError

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Password hashing is deliberately slow, which makes these endpoints a cheap
# CPU-exhaustion target and an online password-guessing target. Limit both.
_WINDOW_SECONDS = 300
_MAX_ATTEMPTS = 10
_attempts: dict[str, list[float]] = {}


def _rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _attempts.get(client_ip, []) if now - t < _WINDOW_SECONDS]
    _attempts[client_ip] = hits
    if len(hits) >= _MAX_ATTEMPTS:
        return True
    hits.append(now)
    return False


class Credentials(BaseModel):
    email: str
    password: str
    # The server the customer is currently looking at, claimed on signup.
    session_token: str | None = None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@router.post("/api/auth/register")
async def register(payload: Credentials, request: Request):
    if _rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="시도가 너무 많습니다. 잠시 후 다시 해주세요.")
    try:
        return accounts.register(payload.email, payload.password, payload.session_token)
    except AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/auth/login")
async def login(payload: Credentials, request: Request):
    if _rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="시도가 너무 많습니다. 잠시 후 다시 해주세요.")
    try:
        return accounts.login(payload.email, payload.password, payload.session_token)
    except AccountError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


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
