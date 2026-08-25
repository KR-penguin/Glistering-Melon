import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from agent.customer_agent import get_server_by_token, handle_customer_message
from app import billing
from app.config import settings

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Chat hits the Anthropic API, which costs real money per call, so the session
# gets its own budget independent of the IP-based limit on server creation.
CHAT_WINDOW_SECONDS = 60
CHAT_MAX_PER_WINDOW = 8
_chat_hits: dict[str, list[float]] = {}


def _chat_rate_limited(token: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _chat_hits.get(token, []) if now - t < CHAT_WINDOW_SECONDS]
    _chat_hits[token] = hits
    if len(hits) >= CHAT_MAX_PER_WINDOW:
        return True
    hits.append(now)
    return False


class ChatRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message is empty")
        return v[:2000]


def _require_server(token: str):
    row = get_server_by_token(token)
    if row is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return row


@router.get("/session/{token}")
async def session_page(token: str):
    _require_server(token)
    return FileResponse(STATIC_DIR / "session.html")


@router.get("/api/session/{token}")
async def session_status(token: str):
    row = _require_server(token)
    return {
        "nickname": row["nickname"],
        "tier": row["tier"],
        "status": row["status"],
        "running": row["status"] == "running",
        "address": f"{settings.minecraft_public_host}:{row['game_port']}",
        "modpack_key": row["modpack_key"],
        "payment": billing.payment_summary(row),
        "support_url": settings.discord_invite_url or None,
        "claimed": bool(row["user_id"]),
    }


@router.post("/api/session/{token}/chat")
async def session_chat(token: str, payload: ChatRequest):
    _require_server(token)
    if _chat_rate_limited(token):
        raise HTTPException(status_code=429, detail="조금 천천히 말씀해주세요.")
    reply = await handle_customer_message(token, payload.message)
    return {"reply": reply}


@router.post("/api/session/{token}/paid")
async def session_claim_paid(token: str):
    _require_server(token)
    return await billing.request_extension(token)
