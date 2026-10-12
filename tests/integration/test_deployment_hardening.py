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


# --------------------------------------------------------------------------- #
# M8: settings persistence and form isolation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_settings_form_isolation(auth_client: AsyncClient, db_session):
    """Saving SMTP must not wipe AI settings (and vice versa)."""
    from app.config import settings

    original_ai_url = settings.AI_BASE_URL

    # Save SMTP form — AI fields should stay untouched.
    resp = await auth_client.post(
        "/admin/settings",
        data={
            "form_type": "smtp",
            "smtp_host": "mail.example.com",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "test@example.com",
            "smtp_use_tls": "True",
            "smtp_subject_template": "Report: {{report_name}}",
            "smtp_body_template": "Please find the attached report: {{report_name}}\n\nGenerated at: {{generated_at}}",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert settings.SMTP_HOST == "mail.example.com"
    # AI should be unchanged (the SMTP form didn't submit AI fields).
    assert settings.AI_BASE_URL == original_ai_url

    # Save AI form — SMTP should stay untouched.
    resp = await auth_client.post(
        "/admin/settings",
        data={
            "form_type": "ai",
            "ai_enabled": "True",
            "ai_base_url": "https://ai.example.com/v1",
            "ai_api_key": "sk-test-key",
            "ai_model": "gpt-4o",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert settings.AI_BASE_URL == "https://ai.example.com/v1"
    # SMTP should be unchanged.
    assert settings.SMTP_HOST == "mail.example.com"


@pytest.mark.asyncio
async def test_settings_persist_in_db(auth_client: AsyncClient, db_session):
    """Values saved via the admin page must be persisted to the DB."""
    from sqlalchemy import select

    from app.models.app_setting import AppSetting

    await auth_client.post(
        "/admin/settings",
        data={
            "form_type": "smtp",
            "smtp_host": "persisted.example.com",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "test@example.com",
            "smtp_use_tls": "True",
            "smtp_subject_template": "Report: {{report_name}}",
            "smtp_body_template": "Please find the attached report: {{report_name}}\n\nGenerated at: {{generated_at}}",
        },
        follow_redirects=False,
    )

    row = await db_session.execute(
        select(AppSetting).where(AppSetting.key == "SMTP_HOST")
    )
    assert row.scalar_one().value == "persisted.example.com"


@pytest.mark.asyncio
async def test_settings_page_does_not_render_ai_api_key(auth_client: AsyncClient):
    """The AI API key must not be echoed back into the HTML page."""
    from app.config import settings

    original = settings.AI_API_KEY
    settings.AI_API_KEY = "sk-1234567890abcdef"
    try:
        resp = await auth_client.get("/admin/settings")
        assert resp.status_code == 200
        # The raw key should not appear in the rendered HTML.
        assert "sk-1234567890abcdef" not in resp.text
    finally:
        settings.AI_API_KEY = original
