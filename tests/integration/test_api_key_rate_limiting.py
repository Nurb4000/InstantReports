"""Integration tests for the API-key management endpoints (Gap #2).

Covers two controls on ``/api-keys``:

* Rate limiting -- a per-client-IP sliding-window limiter returns ``429`` with a
  ``Retry-After`` header once the window is full. The limiter is swapped for a
  fixed-clock one so the 429 path is deterministic and isolated from other tests.
* Permission allow-listing on key creation -- a key can only be created with
  permissions from the allowed set; arbitrary/empty permission strings are
  rejected with ``400`` (the H1 sub-issue).
"""

from __future__ import annotations

import uuid

import pytest

import app.routes.rate_limiting as rl
from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.api_key import APIKey
from app.models.user import AuthSource, User, UserRole
from app.routes.api_keys import get_current_user_from_api_key
from app.services.api_key import generate_api_key
from app.services.ratelimit import RateLimiter
from sqlalchemy import select

_NONEXISTENT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


async def _make_key(db_session, permissions: str) -> str:
    api_key, plaintext = await generate_api_key(
        db=db_session,
        name=f"rl-key-{permissions.replace(',', '-')}",
        user_id=uuid.uuid4(),
        permissions=[p.strip() for p in permissions.split(",")],
    )
    return plaintext


def _headers(plaintext: str) -> dict:
    return {"X-API-Key": plaintext}


@pytest.fixture
def fixed_limiter(monkeypatch):
    """Return a factory that installs a fresh fixed-clock limiter."""

    def _make(max_attempts: int) -> RateLimiter:
        limiter = RateLimiter(
            max_attempts=max_attempts, window_seconds=60, clock=lambda: 0.0
        )
        monkeypatch.setattr(rl, "_api_key_limiter", limiter)
        return limiter

    return _make


# --- rate limiting ---------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_endpoint_blocks_after_exceeding_limit(
    client, db_session, fixed_limiter
):
    key = await _make_key(db_session, "read,write,admin")
    fixed_limiter(max_attempts=2)

    first = await client.get("/api-keys/", headers=_headers(key))
    assert first.status_code == 200, first.text
    second = await client.get("/api-keys/", headers=_headers(key))
    assert second.status_code == 200, second.text

    third = await client.get("/api-keys/", headers=_headers(key))
    assert third.status_code == 429
    assert third.headers.get("retry-after")


@pytest.mark.asyncio
async def test_api_key_create_is_rate_limited(client, db_session, fixed_limiter):
    admin = await _make_admin(db_session)
    # A synthetic principal (plain User) preserves backward-compatible behaviour
    # and is allowed through require_permission("write").
    _override_user(admin)
    fixed_limiter(max_attempts=1)

    first = await client.post("/api-keys/?name=k1")
    assert first.status_code == 200, first.text
    second = await client.post("/api-keys/?name=k2")
    assert second.status_code == 429
    assert second.headers.get("retry-after")


# --- permission allow-listing ----------------------------------------------


@pytest.mark.asyncio
async def test_create_key_rejects_unknown_permission(client, db_session):
    key = await _make_key(db_session, "read,write")
    resp = await client.post(
        "/api-keys/?name=bad&permissions=read,bogus", headers=_headers(key)
    )
    assert resp.status_code == 400
    assert "bogus" in resp.text


@pytest.mark.asyncio
async def test_create_key_rejects_empty_permission(client, db_session):
    key = await _make_key(db_session, "read,write")
    resp = await client.post("/api-keys/?name=empty&permissions=", headers=_headers(key))
    assert resp.status_code == 400
    assert "permission" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_key_normalises_and_accepts_valid_permissions(
    client, db_session
):
    key = await _make_key(db_session, "read,write")
    resp = await client.post(
        "/api-keys/?name=ok&permissions=READ,EXECUTE", headers=_headers(key)
    )
    assert resp.status_code == 200, resp.text
    # Stored/echoed in canonical lowercase form.
    stored = (
        await db_session.execute(
            select(APIKey).where(APIKey.name == "ok")
        )
    ).scalar().permissions
    assert stored == "read,execute"


# --- helpers ---------------------------------------------------------------


async def _make_admin(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin@x.test",
        name="Admin",
        password_hash=hash_password("pw"),
        role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _override_user(user: User) -> None:
    async def _dep():
        return user

    fastapi_app.dependency_overrides[get_current_user_from_api_key] = _dep
