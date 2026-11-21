"""Integration tests for heavy-endpoint rate limiting (Gap #1).

Covers the end-to-end behaviour of the shared limiter wired into
``preview_export``: requests under the quota reach the handler, and once the
per-client window is full the route returns ``429 Too Many Requests`` with a
``Retry-After`` header. The limiter is swapped for a fixed-clock one so the
window never expires mid-test.
"""

from __future__ import annotations

import json
import uuid

import pytest

import app.routes.rate_limiting as rl
from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional
from app.services.ratelimit import RateLimiter


def _make_user(role: UserRole, email: str | None = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=email or f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
        name=role.value,
        password_hash=hash_password("pw"),
        role=role,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


def _override_user(user: User) -> None:
    async def _dep():
        return user

    fastapi_app.dependency_overrides[get_current_user_optional] = _dep


def _fixed_limiter(max_attempts: int) -> RateLimiter:
    return RateLimiter(max_attempts=max_attempts, window_seconds=60, clock=lambda: 0.0)


_EMPTY_DEFINITION = json.dumps(
    {"layout": {"sections": []}, "data_sources": [], "parameters": []}
)


@pytest.mark.asyncio
async def test_preview_export_blocks_after_exceeding_limit(client, db_session, monkeypatch):
    admin = _make_user(UserRole.ADMIN, "rl_export@x.test")
    db_session.add(admin)
    await db_session.commit()
    # Two requests allowed per fixed 60s window; the third is blocked.
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=2))
    _override_user(admin)

    params = {"definition_json": _EMPTY_DEFINITION, "format": "pdf"}

    first = await client.get("/preview/export", params=params)
    assert first.status_code != 429, first.text
    second = await client.get("/preview/export", params=params)
    assert second.status_code != 429, second.text

    third = await client.get("/preview/export", params=params)
    assert third.status_code == 429
    assert third.headers.get("retry-after")


@pytest.mark.asyncio
async def test_preview_export_under_limit_still_serves(client, db_session, monkeypatch):
    admin = _make_user(UserRole.ADMIN, "rl_ok@x.test")
    db_session.add(admin)
    await db_session.commit()
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=5))
    _override_user(admin)

    params = {"definition_json": _EMPTY_DEFINITION, "format": "pdf"}
    resp = await client.get("/preview/export", params=params)
    # Not rate limited; a real export response (200) or handler-level error, but
    # never a limiter 429 within the quota.
    assert resp.status_code != 429, resp.text


@pytest.mark.asyncio
async def test_ai_endpoint_also_rate_limited(client, db_session, monkeypatch):
    """The AI routes share the same per-client budget, so an attacker cannot
    bypass the limit by switching from export to an AI call."""
    admin = _make_user(UserRole.ADMIN, "rl_ai@x.test")
    db_session.add(admin)
    await db_session.commit()
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=1))
    _override_user(admin)

    first = await client.post("/ai/generate-sql", json={"prompt": "what are sales?"})
    assert first.status_code != 429, first.text
    second = await client.post("/ai/generate-sql", json={"prompt": "and trends?"})
    assert second.status_code == 429
    assert second.headers.get("retry-after")
