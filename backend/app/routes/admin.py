"""Owner-facing endpoints, replacing the Discord bot.

The bot was only ever a delivery mechanism for two things: confirming a
deposit and seeing who still owes money. A bot means a gateway connection to
keep alive, a token to manage, and discord.py in the dependency tree — all of
it to move a handful of admin actions. A token-guarded HTTP endpoint does the
same job with nothing to keep running.

Outbound Discord notifications are unaffected: those go through a webhook,
which needs no bot.

Auth is a single shared token in ADMIN_TOKEN, compared in constant time. That
is appropriate for one operator on a LAN, not for a multi-admin service — if
this ever grows past one person, replace it with real accounts rather than
handing the token around.
"""

import secrets

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from agent import tools
from app.config import settings
from app.routes.session import STATIC_DIR

router = APIRouter()


def _require_admin(token: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured on the server.")
    if not token or not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid admin token")


class ConfirmPaymentRequest(BaseModel):
    payment_code: str

    @field_validator("payment_code")
    @classmethod
    def four_digits(cls, v: str) -> str:
        v = v.strip()
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("payment_code must be 4 digits")
        return v


@router.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@router.get("/api/admin/pending")
async def pending_payments(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    return {"pending": tools.list_awaiting_payment()}


@router.get("/api/admin/servers")
async def all_servers(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    return {"servers": tools.list_servers()}


@router.post("/api/admin/confirm-payment")
async def confirm_payment(
    payload: ConfirmPaymentRequest, x_admin_token: str | None = Header(default=None)
):
    _require_admin(x_admin_token)
    result = await tools.confirm_payment(payload.payment_code)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return result


class AskRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message is empty")
        return v[:2000]


@router.post("/api/admin/ask")
async def ask_ops_agent(
    payload: AskRequest, x_admin_token: str | None = Header(default=None)
):
    """Natural-language ops, replacing what the Discord bot used to carry."""
    _require_admin(x_admin_token)
    from agent.ops_agent import handle_ops_message

    return {"reply": await handle_ops_message(payload.message)}
