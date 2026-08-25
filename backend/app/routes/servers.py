from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator

from app import provisioning
from app.capacity import TIER_SPECS
from app.modpacks import MODPACK_CATALOG, is_valid_modpack_key
from app.mojang import is_valid_java_username
from app.ratelimit import is_rate_limited

router = APIRouter()


class CreateServerRequest(BaseModel):
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


@router.get("/api/plans")
async def list_plans():
    """Lets the landing page render tier choices without hardcoding them twice."""
    from app.billing import TIER_PRICES

    return {
        "plans": [
            {"key": key, "ram_mb": spec["ram_mb"], "storage_gb": spec["storage_gb"],
             "price_krw": TIER_PRICES[key]}
            for key, spec in TIER_SPECS.items()
        ],
        "modpacks": [
            {"key": key, "name": pack.name} for key, pack in MODPACK_CATALOG.items()
        ],
    }


@router.post("/api/servers")
async def create_server(payload: CreateServerRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="너무 많이 요청하셨어요. 잠시 후 다시 시도해주세요.")

    if not is_valid_modpack_key(payload.modpack_key):
        raise HTTPException(status_code=400, detail="알 수 없는 모드팩입니다.")

    if not await is_valid_java_username(payload.nickname):
        raise HTTPException(
            status_code=400, detail="마인크래프트 자바 에디션에 없는 닉네임이에요. 다시 확인해주세요."
        )

    result = await provisioning.submit_application(
        nickname=payload.nickname,
        email=payload.email,
        tier=payload.tier,
        modpack_key=payload.modpack_key,
    )

    if result["status"] == "no_capacity":
        raise HTTPException(
            status_code=503,
            detail="지금은 여유 서버 자원이 없어요. 자리가 나는 대로 이메일로 안내드릴게요.",
        )

    return result
