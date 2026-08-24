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


async def send_provision_failed_email(to: str, nickname: str) -> None:
    body = (
        f"{nickname}님, 죄송해요.\n\n"
        "서버를 여는 중에 문제가 생겨서 직접 확인 후 다시 연락드릴게요.\n\n"
        "- Glistering Melon"
    )
    await asyncio.to_thread(_send_sync, to, "[Glistering Melon] 서버 오픈 지연 안내", body)
