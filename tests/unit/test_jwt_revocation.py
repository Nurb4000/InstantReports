"""Unit + integration tests for JWT revocation (H3).

JWTs are stateless, so a stolen token stays valid until it expires. These tests
verify that logout records the token's ``jti`` in the revoked_tokens table and
that subsequent requests presenting that token are rejected.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    is_token_revoked,
    revoke_access_token,
)
from app.routes.auth import logout
from app.models.user import AuthSource, User, UserRole


class _Request:
    """Minimal stand-in for fastapi.Request with just what logout reads."""

    def __init__(self, cookies: dict | None = None, headers: dict | None = None):
        self.cookies = cookies or {}
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_create_access_token_carries_unique_jti():
    t1 = decode_access_token(create_access_token(data={"sub": "a@x.com"}))
    t2 = decode_access_token(create_access_token(data={"sub": "a@x.com"}))
    assert t1 and t2
    assert "jti" in t1 and "jti" in t2
    assert t1["jti"] != t2["jti"]


@pytest.mark.asyncio
async def test_revocation_lifecycle(db_session: AsyncSession):
    token = create_access_token(data={"sub": "revoked@example.com"})
    payload = decode_access_token(token)
    jti = payload["jti"]
    exp = payload["exp"]

    assert await is_token_revoked(db_session, jti) is False

    await revoke_access_token(db_session, jti, payload["sub"], exp)
    assert await is_token_revoked(db_session, jti) is True

    # Idempotent: revoking again must not raise.
    await revoke_access_token(db_session, jti, payload["sub"], exp)
    assert await is_token_revoked(db_session, jti) is True


@pytest.mark.asyncio
async def test_get_current_user_rejects_revoked_token(db_session: AsyncSession):
    db_session.add(
        User(
            id=uuid.uuid4(),
            email="revoked@example.com",
            name="Revoked",
            password_hash="x",
            role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL,
            is_active=True,
        )
    )
    await db_session.commit()

    token = create_access_token(data={"sub": "revoked@example.com"})

    # Valid (not yet revoked) -> user resolved.
    assert await get_current_user(token=token, db=db_session) is not None

    payload = decode_access_token(token)
    await revoke_access_token(db_session, payload["jti"], payload["sub"], payload["exp"])

    # After revocation -> None even though the JWT signature/exp are still valid.
    assert await get_current_user(token=token, db=db_session) is None


@pytest.mark.asyncio
async def test_logout_revokes_token(db_session: AsyncSession):
    token = create_access_token(data={"sub": "logout@example.com"})
    payload = decode_access_token(token)

    resp = await logout(_Request(cookies={"access_token": token}), db=db_session)
    assert resp.status_code == 302

    assert await is_token_revoked(db_session, payload["jti"]) is True


@pytest_asyncio.fixture
async def logged_in_and_out(client: AsyncClient, db_session: AsyncSession):
    """Yield (client, raw_token) after the user has logged out over HTTP."""
    user = User(
        id=uuid.uuid4(),
        email="after_logout@example.com",
        name="After",
        password_hash=hash_password("x"),
        role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    login = await client.post(
        "/auth/login",
        data={"email": "after_logout@example.com", "password": "x"},
        follow_redirects=False,
    )
    raw_token = login.cookies.get("access_token")
    assert raw_token

    # Log out — this should revoke the token server-side.
    await client.get("/auth/logout", headers={"Cookie": f"access_token={raw_token}"})

    yield client, raw_token


@pytest.mark.asyncio
async def test_revoked_token_rejected_on_protected_endpoint(logged_in_and_out):
    client, raw_token = logged_in_and_out

    # Present the now-revoked token as a Bearer credential on a protected route.
    # The fixture user is an admin, so a valid token returns 200 here; once the
    # token is revoked ``get_current_user_optional`` resolves to None and the
    # route denies access (403), proving the server-side revocation took effect.
    resp = await client.get(
        "/datasources/", headers={"Authorization": f"Bearer {raw_token}"}
    )
    assert resp.status_code == 403, resp.text
