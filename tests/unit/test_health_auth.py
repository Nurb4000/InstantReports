"""Unit tests for the D3 fix: optional auth on the /health probe.

When HEALTH_CHECK_TOKEN is configured, /health requires it (Bearer header or
?token= query param); otherwise it stays public for probes that cannot carry a
secret.
"""

import pytest_asyncio

from app.config import settings as app_settings


# Uses the conftest `client` fixture, which overrides get_db with the sqlite
# test session so the health probe's DB ping succeeds.
class TestHealthAuth:
    """D3: /health token gate."""

    async def test_public_when_token_unset(self, client, monkeypatch):
        """Default (no token configured) stays public for probes."""
        monkeypatch.setattr(app_settings, "HEALTH_CHECK_TOKEN", "", raising=True)
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_requires_token_when_configured(self, client, monkeypatch):
        """Without a token and one configured -> 401."""
        monkeypatch.setattr(app_settings, "HEALTH_CHECK_TOKEN", "s3cr3t", raising=True)
        resp = await client.get("/health")
        assert resp.status_code == 401

    async def test_wrong_token_rejected(self, client, monkeypatch):
        """A wrong token -> 401."""
        monkeypatch.setattr(app_settings, "HEALTH_CHECK_TOKEN", "s3cr3t", raising=True)
        resp = await client.get("/health", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    async def test_valid_token_via_header(self, client, monkeypatch):
        """Correct Bearer token proceeds past the gate (200 in test env)."""
        monkeypatch.setattr(app_settings, "HEALTH_CHECK_TOKEN", "s3cr3t", raising=True)
        resp = await client.get("/health", headers={"Authorization": "Bearer s3cr3t"})
        assert resp.status_code != 401

    async def test_valid_token_via_query_param(self, client, monkeypatch):
        """Correct token via ?token= proceeds past the gate."""
        monkeypatch.setattr(app_settings, "HEALTH_CHECK_TOKEN", "s3cr3t", raising=True)
        resp = await client.get("/health", params={"token": "s3cr3t"})
        assert resp.status_code != 401

    async def test_token_case_insensitive_scheme(self, client, monkeypatch):
        """'bearer' (lowercase) scheme is accepted."""
        monkeypatch.setattr(app_settings, "HEALTH_CHECK_TOKEN", "s3cr3t", raising=True)
        resp = await client.get("/health", headers={"Authorization": "bearer s3cr3t"})
        assert resp.status_code != 401
