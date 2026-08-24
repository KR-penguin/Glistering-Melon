from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator

from app import provisioning
from app.capacity import TIER_SPECS
from app.modpacks import is_valid_modpack_key
from app.mojang import is_valid_java_username
from app.ratelimit import is_rate_limited

router = APIRouter()


class ApplicationRequest(BaseModel):
    nickname: str
    email: EmailStr
    tier: str
    modpack_key: str | None = None

    @field_validator("nickname")
    @classmethod
    def nickname_shape(cls, v: str) -> str:
        v = v.strip()
        if not (3 <= len(v) <= 16):
            raise ValueError("nickname must be 3-16 characters")
        return v

    @field_validator("tier")
    @classmethod
    def tier_known(cls, v: str) -> str:
        if v not in TIER_SPECS:
            raise ValueError(f"unknown tier: {v}")
        return v


@router.post("/api/applications")
async def create_application(payload: ApplicationRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="too many requests, try again later")

    if not is_valid_modpack_key(payload.modpack_key):
        raise HTTPException(status_code=400, detail="unknown modpack_key")

    if not await is_valid_java_username(payload.nickname):
        raise HTTPException(status_code=400, detail="nickname is not a real Java Edition username")

    result = await provisioning.submit_application(
        nickname=payload.nickname,
        email=payload.email,
        tier=payload.tier,
        modpack_key=payload.modpack_key,
    )
    return result
