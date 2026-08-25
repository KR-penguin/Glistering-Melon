"""A dead support link is worse than no link when someone's payment is stuck,
so an unconfigured invite must hide the links rather than render a broken one.
"""

from app import email_notify
from app.config import Settings


def test_support_line_is_empty_when_unset(monkeypatch):
    monkeypatch.setattr(email_notify.settings, "discord_invite_url", "")
    assert email_notify._support_line() == ""


def test_support_line_included_when_set(monkeypatch):
    monkeypatch.setattr(email_notify.settings, "discord_invite_url", "https://discord.gg/abc123")
    line = email_notify._support_line()
    assert "https://discord.gg/abc123" in line
    assert line.endswith("\n")


def test_invite_url_reads_from_env(monkeypatch):
    monkeypatch.setenv("DISCORD_INVITE_URL", "https://discord.gg/xyz")
    assert Settings(_env_file=None).discord_invite_url == "https://discord.gg/xyz"


def test_invite_url_defaults_blank():
    assert Settings(_env_file=None).discord_invite_url == ""
