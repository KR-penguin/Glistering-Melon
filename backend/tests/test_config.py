"""A fresh deploy leaves optional credentials blank in .env. That must
degrade gracefully rather than crash-looping the container and taking the
provisioning pipeline down with it.
"""

import pytest

from app.config import Settings


def test_blank_budget_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HOST_RAM_MB_BUDGET", "")
    monkeypatch.setenv("MAX_CONCURRENT_SERVERS", "   ")
    s = Settings(_env_file=None)
    assert s.host_ram_mb_budget == 24576
    assert s.max_concurrent_servers == 6


def test_real_values_still_parse(monkeypatch):
    monkeypatch.setenv("HOST_RAM_MB_BUDGET", "8192")
    s = Settings(_env_file=None)
    assert s.host_ram_mb_budget == 8192


def test_genuinely_invalid_value_still_rejected(monkeypatch):
    monkeypatch.setenv("HOST_RAM_MB_BUDGET", "not-a-number")
    with pytest.raises(Exception):
        Settings(_env_file=None)
