"""The admin endpoints replace the Discord bot, so they are now the only way
to confirm a payment. Their auth is the whole security boundary for actions
that start and stop customers' servers.
"""

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.routes import admin


def test_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(admin.settings, "admin_token", "s3cret")
    with pytest.raises(HTTPException) as exc:
        admin._require_admin(None)
    assert exc.value.status_code == 401


def test_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(admin.settings, "admin_token", "s3cret")
    with pytest.raises(HTTPException) as exc:
        admin._require_admin("nope")
    assert exc.value.status_code == 401


def test_accepts_correct_token(monkeypatch):
    monkeypatch.setattr(admin.settings, "admin_token", "s3cret")
    admin._require_admin("s3cret")  # must not raise


def test_unconfigured_token_refuses_rather_than_allowing(monkeypatch):
    """An empty ADMIN_TOKEN must not mean 'no auth required' — that would
    leave every admin action wide open on a fresh deploy."""
    monkeypatch.setattr(admin.settings, "admin_token", "")
    with pytest.raises(HTTPException) as exc:
        admin._require_admin("")
    assert exc.value.status_code == 503

    with pytest.raises(HTTPException):
        admin._require_admin("anything")


def test_payment_code_must_be_four_digits():
    from pydantic import ValidationError

    admin.ConfirmPaymentRequest(payment_code="0421")
    for bad in ("abc", "12", "12345", ""):
        with pytest.raises(ValidationError):
            admin.ConfirmPaymentRequest(payment_code=bad)


def test_admin_token_reads_from_env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "from-env")
    assert Settings(_env_file=None).admin_token == "from-env"
