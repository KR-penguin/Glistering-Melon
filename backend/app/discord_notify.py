import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

COLOR_INFO = 15478803  # site accent (#EC3013)
COLOR_OK = 3066993  # green
COLOR_WARN = 15158332  # red


async def _send_embed(title: str, description: str, color: int, fields: list[dict] | None = None) -> None:
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set, skipping notification: %s", title)
        return
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.discord_webhook_url, json=payload)
    except httpx.HTTPError:
        logger.exception("failed to post Discord notification: %s", title)


async def notify_new_application(nickname: str, email: str, tier: str, modpack_key: str | None) -> None:
    fields = [
        {"name": "닉네임", "value": nickname, "inline": True},
        {"name": "이메일", "value": email, "inline": True},
        {"name": "플랜", "value": tier, "inline": True},
    ]
    if modpack_key:
        fields.append({"name": "모드팩", "value": modpack_key, "inline": True})
    await _send_embed("새 서버 신청", "신청이 접수됐습니다.", COLOR_INFO, fields)


async def notify_capacity_alert(nickname: str, tier: str) -> None:
    await _send_embed(
        "용량 부족",
        f"{nickname}님의 {tier} 신청을 받았지만 여유 용량이 없어요. 대기 안내 메일을 보냈습니다.",
        COLOR_WARN,
    )


async def notify_provisioned(nickname: str, container_name: str, game_port: int) -> None:
    await _send_embed(
        "서버 오픈 완료",
        f"{nickname}님 서버가 열렸습니다.",
        COLOR_OK,
        [
            {"name": "컨테이너", "value": container_name, "inline": True},
            {"name": "포트", "value": str(game_port), "inline": True},
        ],
    )


async def notify_provision_failed(nickname: str, container_name: str, error: str) -> None:
    await _send_embed(
        "서버 오픈 실패",
        f"{nickname}님 서버({container_name})를 여는 중 오류가 발생했어요. 직접 확인이 필요합니다.",
        COLOR_WARN,
        [{"name": "오류", "value": error[:500], "inline": False}],
    )


async def notify_watchdog_restart(container_name: str) -> None:
    await _send_embed(
        "자동 재시작 감지",
        f"{container_name} 컨테이너가 예기치 않게 재시작됐어요 (자동 복구됨).",
        COLOR_WARN,
    )
