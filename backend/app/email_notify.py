"""Applicant emails via the founder's own Gmail account over SMTP + an App
Password — free within Gmail's sending quota, no third-party transactional
email API (per the founder's cost constraint on the notification pipeline).
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _send_sync(to: str, subject: str, body: str) -> None:
    if not settings.gmail_address or not settings.gmail_app_password:
        logger.warning("Gmail credentials not set, skipping email: %s", subject)
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.gmail_address
    msg["To"] = to
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(settings.gmail_address, settings.gmail_app_password)
        server.send_message(msg)


async def send_no_capacity_email(to: str, nickname: str) -> None:
    body = (
        f"{nickname}님, 안녕하세요.\n\n"
        "지금은 신청이 몰려서 여유 서버 자원이 없어요. "
        "자리가 나는 대로 순서대로 다시 안내드릴게요.\n\n"
        "- Glistering Melon"
    )
    await asyncio.to_thread(_send_sync, to, "[Glistering Melon] 대기 안내", body)


async def send_provisioned_email(to: str, nickname: str, game_address: str) -> None:
    body = (
        f"{nickname}님, 서버가 열렸어요!\n\n"
        f"접속 주소: {game_address}\n\n"
        "결제(계좌입금) 안내는 별도로 다시 연락드릴게요.\n\n"
        "- Glistering Melon"
    )
    await asyncio.to_thread(_send_sync, to, "[Glistering Melon] 서버 오픈 완료", body)


def _support_line() -> str:
    if not settings.discord_invite_url:
        return ""
    return f"문의: {settings.discord_invite_url}\n"


async def send_server_ready_email(
    to: str, nickname: str, game_address: str, session_url: str, price_krw: int, payment_code: str
) -> None:
    body = (
        f"{nickname}님, 서버가 열렸어요!\n\n"
        f"접속 주소: {game_address}\n"
        f"관리 페이지: {session_url}\n\n"
        f"[입금 안내]\n"
        f"금액: {price_krw:,}원\n"
        f"계좌: 경남은행 2070218854605\n"
        f"입금자명: 본인이름 뒤에 {payment_code} 를 붙여주세요 (예: 홍길동{payment_code})\n\n"
        f"코드를 못 붙이셨거나 부모님 등 다른 분 이름으로 보내셨다면, "
        f"어느 서버 입금인지 찾기 어려우니 아래 문의처로 알려주세요.\n\n"
        "입금이 확인되지 않으면 서버가 잠시 정지됩니다. "
        "월드는 지워지지 않고, 입금 확인 즉시 다시 켜집니다.\n"
        "이미 입금하셨다면 관리 페이지에서 '입금했어요'를 눌러주세요.\n\n"
        f"{_support_line()}"
        "- Glistering Melon"
    )
    await asyncio.to_thread(_send_sync, to, "[Glistering Melon] 서버가 열렸습니다", body)


async def send_payment_expired_email(to: str, nickname: str, session_url: str) -> None:
    body = (
        f"{nickname}님, 입금이 확인되지 않아 서버를 잠시 정지했어요.\n\n"
        "월드 데이터는 그대로 보존돼 있습니다. "
        "입금이 확인되면 자동으로 다시 켜집니다.\n\n"
        f"관리 페이지: {session_url}\n"
        "이미 입금하셨다면 관리 페이지에서 '입금했어요'를 눌러주세요.\n"
        "입금하셨는데도 정지됐다면 입금자명에 코드가 빠졌을 수 있어요. "
        "문의처로 알려주시면 바로 확인해드립니다.\n\n"
        f"{_support_line()}"
        "- Glistering Melon"
    )
    await asyncio.to_thread(_send_sync, to, "[Glistering Melon] 서버가 정지됐습니다", body)


async def send_provision_failed_email(to: str, nickname: str) -> None:
    body = (
        f"{nickname}님, 죄송해요.\n\n"
        "서버를 여는 중에 문제가 생겨서 직접 확인 후 다시 연락드릴게요.\n\n"
        "- Glistering Melon"
    )
    await asyncio.to_thread(_send_sync, to, "[Glistering Melon] 서버 오픈 지연 안내", body)
