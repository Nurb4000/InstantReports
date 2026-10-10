"""Security-hardening regression tests for P1.2."""
from __future__ import annotations

import pytest


def test_secret_key_rejects_weak_values(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "change-me-in-production")
    with pytest.raises(ValueError):
        from app import config

        importlib = __import__("importlib")
        importlib.reload(config)

    monkeypatch.setenv("SECRET_KEY", "admin")
    with pytest.raises(ValueError):
        from app import config
        importlib.reload(config)

    monkeypatch.setenv("SECRET_KEY", "abcdef12")
    with pytest.raises(ValueError):
        from app import config
        importlib.reload(config)

    monkeypatch.setenv("SECRET_KEY", "a" * 15)
    with pytest.raises(ValueError):
        from app import config
        importlib.reload(config)

    monkeypatch.setenv("SECRET_KEY", "a" * 16)
    from app import config
    importlib.reload(config)
    assert config.settings.SECRET_KEY == "a" * 16


def test_secret_key_accepts_strong(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    from app import config
    importlib = __import__("importlib")
    importlib.reload(config)
    assert config.settings.SECRET_KEY == "x" * 32
