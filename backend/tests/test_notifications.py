"""Notifications that ask the owner to DO something must point somewhere real.

Removing the Discord bot left the payment-claim alert still saying "reply
`3409 입금확인` in Discord" — instructions for a listener that no longer
existed, so the owner followed them and nothing happened. These tests pin the
alerts to the admin page instead.
"""

import pytest

from app import discord_notify


@pytest.fixture
def sent(monkeypatch):
    captured = []

    async def fake_send(title, description, color, fields=None):
        captured.append({"title": title, "description": description, "fields": fields or []})

    monkeypatch.setattr(discord_notify, "_send_embed", fake_send)
    monkeypatch.setattr(discord_notify.settings, "public_base_url", "http://example.test:8000")
    return captured


def test_admin_url_is_built_from_the_public_base():
    from app.config import settings

    assert discord_notify._admin_url().endswith("/admin")
    assert settings.public_base_url.rstrip("/") in discord_notify._admin_url()


def test_admin_url_does_not_double_slash(monkeypatch):
    monkeypatch.setattr(discord_notify.settings, "public_base_url", "http://example.test:8000/")
    assert discord_notify._admin_url() == "http://example.test:8000/admin"


async def test_payment_claim_points_at_the_admin_page(sent):
    await discord_notify.notify_payment_claim("kopeng", "3409", 4900, repeat=False)
    body = sent[0]["description"]
    assert "http://example.test:8000/admin" in body
    assert "3409" in body


async def test_payment_claim_does_not_ask_for_a_discord_reply(sent):
    """There is no bot listening; telling the owner to reply is a dead end."""
    await discord_notify.notify_payment_claim("kopeng", "3409", 4900, repeat=False)
    body = sent[0]["description"]
    assert "디스코드에" not in body
    assert "입금확인` 이라고" not in body


async def test_expiry_notice_says_how_to_bring_the_server_back(sent):
    await discord_notify.notify_payment_expired("kopeng", "3409", 4900)
    body = sent[0]["description"]
    assert "http://example.test:8000/admin" in body
    assert "보존" in body, "must reassure that the world is not deleted"


async def test_repeat_claim_is_marked_as_such(sent):
    await discord_notify.notify_payment_claim("kopeng", "3409", 4900, repeat=True)
    assert "재요청" in sent[0]["title"]
