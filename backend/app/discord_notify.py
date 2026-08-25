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


async def notify_payment_claim(
    nickname: str, payment_code: str | None, price_krw: int | None, repeat: bool
) -> None:
    """Customer says they paid. Payment confirmation is manual, so this is the
    ping that has to actually reach the founder — it is the safety valve for a
    real deposit landing while nobody is watching the bank app."""
    title = "입금했다고 합니다 (재요청)" if repeat else "입금했다고 합니다 — 확인 필요"
    await _send_embed(
        title,
        f"{nickname}님이 입금 완료를 알렸습니다. 은행 앱에서 확인 후 디스코드에 "
        f"`{payment_code} 입금확인` 이라고 알려주세요.",
        COLOR_WARN,
        [
            {"name": "입금코드", "value": str(payment_code), "inline": True},
            {"name": "금액", "value": f"{price_krw:,}원" if price_krw else "-", "inline": True},
        ],
    )


async def notify_payment_expired(
    nickname: str, payment_code: str | None, price_krw: int | None
) -> None:
    await _send_embed(
        "미입금으로 서버 정지됨",
        f"{nickname}님 서버를 정지했습니다. 월드는 그대로 보존되며, 입금 확인 시 자동으로 다시 켜집니다.",
        COLOR_WARN,
        [
            {"name": "입금코드", "value": str(payment_code), "inline": True},
            {"name": "금액", "value": f"{price_krw:,}원" if price_krw else "-", "inline": True},
        ],
    )


async def notify_watchdog_restart(container_name: str) -> None:
    await _send_embed(
        "자동 재시작 감지",
        f"{container_name} 컨테이너가 예기치 않게 재시작됐어요 (자동 복구됨).",
        COLOR_WARN,
    )
