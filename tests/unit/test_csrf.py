"""Tests for CSRF protection (H4).

Enforcement is gated behind ``settings.CSRF_ENABLED``; the ``csrf_enabled``
fixture flips it on and overrides the ``get_current_user_optional`` dependency
to return an admin. Overriding a *dependency* (rather than the endpoint
function) is required: this FastAPI version binds the endpoint callable at route
registration, so endpoint-function overrides set afterwards are not applied.

With an admin resolved, ``POST /admin/schedules`` proceeds past auth and returns
400 for a missing ``report_id``. That lets us distinguish a 403 raised by the
CSRF middleware ("CSRF token missing or mismatched") from one raised by the
endpoint's own authorization check.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.auth import create_access_token
from app.config import settings
from app.main import app as fastapi_app
from app.routes.auth import get_current_user_optional
from app.models.user import AuthSource, User, UserRole


@pytest_asyncio.fixture
async def csrf_enabled(client: AsyncClient):
    """Enable CSRF enforcement and resolve the current user to an admin."""
    original = settings.CSRF_ENABLED
    settings.CSRF_ENABLED = True

    async def _admin_user():
        return User(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin",
            role=UserRole.ADMIN,
            auth_source=AuthSource.LOCAL,
            is_active=True,
        )

    fastapi_app.dependency_overrides[get_current_user_optional] = _admin_user
    try:
        yield client
    finally:
        settings.CSRF_ENABLED = original
        fastapi_app.dependency_overrides.pop(get_current_user_optional, None)


async def _establish_token(client: AsyncClient) -> str:
    """GET a page to obtain the session csrf_token, then pin it on the client."""
    resp = await client.get("/")
    token = resp.cookies.get("csrf_token")
    assert token
    client.cookies.set("csrf_token", token)
    return token


async def test_get_page_sets_cookie_and_meta_tag(csrf_enabled):
    client = csrf_enabled
    resp = await client.get("/")
    assert resp.status_code == 200

    token = resp.cookies.get("csrf_token")
    assert token
    # The token is embedded in the <meta> tag with the same value as the cookie.
    assert f'name="csrf-token" content="{token}"' in resp.text
    # And the client will resend the cookie on later requests.
    assert client.cookies.get("csrf_token") == token


async def test_state_change_without_token_is_403(csrf_enabled):
    client = csrf_enabled
    await _establish_token(client)

    resp = await client.post("/admin/schedules")  # cookie present, no token
    assert resp.status_code == 403, resp.text
    assert "CSRF" in resp.text


async def test_state_change_with_valid_header_token_ok(csrf_enabled):
    client = csrf_enabled
    token = await _establish_token(client)

    # Valid token in the header -> passes the CSRF gate and reaches the endpoint
    # (which then 400s for the missing report_id, i.e. NOT a CSRF rejection).
    resp = await client.post("/admin/schedules", headers={"X-CSRF-Token": token})
    assert "CSRF" not in resp.text, resp.text
    assert resp.status_code == 400, resp.text


async def test_state_change_with_valid_form_token_ok(csrf_enabled):
    client = csrf_enabled
    token = await _establish_token(client)

    # No header; the token arrives as a form field (server-rendered hidden input).
    resp = await client.post("/admin/schedules", data={"csrf_token": token})
    assert "CSRF" not in resp.text, resp.text
    assert resp.status_code == 400, resp.text


async def test_state_change_with_mismatched_token_403(csrf_enabled):
    client = csrf_enabled
    await _establish_token(client)

    resp = await client.post(
        "/admin/schedules", headers={"X-CSRF-Token": "not-the-token"}
    )
    assert resp.status_code == 403, resp.text
    assert "CSRF" in resp.text


async def test_login_is_exempt_from_csrf(csrf_enabled):
    """POST /auth/login has no session yet, so it must not require a CSRF token."""
    client = csrf_enabled
    # Invalid credentials -> the request passes the CSRF gate and reaches login.
    resp = await client.post(
        "/auth/login", data={"email": "no@x.com", "password": "wrong"}
    )
    assert resp.status_code == 401, resp.text  # login failure, not a CSRF 403


async def test_header_auth_is_exempt_from_csrf(csrf_enabled):
    """A Bearer-JWT request authenticates via header, so it needs no CSRF token."""
    client = csrf_enabled
    await _establish_token(client)
    bearer = create_access_token(data={"sub": "api@example.com"})

    resp = await client.post(
        "/admin/schedules", headers={"Authorization": f"Bearer {bearer}"}
    )
    # Exempt from CSRF; reaches the endpoint and 400s for missing report_id.
    assert "CSRF" not in resp.text, resp.text
    assert resp.status_code == 400, resp.text


async def test_safe_methods_not_blocked(csrf_enabled):
    """GET requires no CSRF token even without the cookie."""
    client = csrf_enabled
    resp = await client.get("/")
    assert resp.status_code == 200, resp.text
