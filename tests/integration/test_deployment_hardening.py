"""Deployment-hardening regressions (finding M21).

Covers the health probe, auth-cookie flags, and the CORS default that shipped
with ``allow_origins=["*"]`` alongside ``allow_credentials=True``.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db


@pytest.mark.asyncio
async def test_health_returns_503_when_db_unreachable(client: AsyncClient):
    """A reachable process with a dead database must report unhealthy, not ok."""
    from app.main import app

    class _BrokenSession:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("database is down")

    async def _broken_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = _broken_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_login_cookie_is_secure(client: AsyncClient, test_user):
    """The auth cookies must carry the Secure flag by default (HTTPS-only)."""
    response = await client.post(
        "/auth/login",
        data={"email": "user@example.com", "password": "password123"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    set_cookie = "; ".join(response.headers.get_list("set-cookie"))
    assert "access_token=" in set_cookie
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie


@pytest.mark.asyncio
async def test_cors_does_not_allow_arbitrary_origins(client: AsyncClient):
    """With no CORS_ALLOW_ORIGINS configured, no cross-origin grant is emitted."""
    response = await client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers
