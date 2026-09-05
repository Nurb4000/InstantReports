"""Tests for datasources route authn/authz.

Covers the round-7 fix: GET /datasources/{id} returns the full connection
``config`` (which holds credentials such as DB passwords). That endpoint must
be designer-gated like every other config-touching endpoint, otherwise any
authenticated user can read another connection's secrets.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.connection import DataConnection
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional


def _user(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{role.value}@example.com",
        name=role.value,
        password_hash=hash_password("pw"),
        role=role,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_get_connection_requires_auth(client, db_session):
    async def _no_user():
        return None

    fastapi_app.dependency_overrides[get_current_user_optional] = _no_user
    conn = DataConnection(
        name="db", connector_type="postgresql",
        config={"password": "s3cret"}, created_by=_user(UserRole.DESIGNER).id,
    )
    db_session.add(conn)
    await db_session.commit()

    resp = await client.get(f"/datasources/{conn.id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_connection_denies_non_designer_and_leaks_no_config(
    client, db_session,
):
    """A viewer must not be able to read a connection's credentials."""
    async def _viewer():
        return _user(UserRole.VIEWER)

    fastapi_app.dependency_overrides[get_current_user_optional] = _viewer

    conn = DataConnection(
        name="db", connector_type="postgresql",
        config={"password": "s3cret"}, created_by=_user(UserRole.DESIGNER).id,
    )
    db_session.add(conn)
    await db_session.commit()

    resp = await client.get(f"/datasources/{conn.id}")
    assert resp.status_code == 403
    assert "s3cret" not in resp.text


@pytest.mark.asyncio
async def test_get_connection_allows_designer(client, db_session):
    async def _designer():
        return _user(UserRole.DESIGNER)

    fastapi_app.dependency_overrides[get_current_user_optional] = _designer

    conn = DataConnection(
        name="db", connector_type="postgresql",
        config={"host": "h", "password": "s3cret"},
        created_by=_user(UserRole.DESIGNER).id,
    )
    db_session.add(conn)
    await db_session.commit()

    resp = await client.get(f"/datasources/{conn.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config"]["password"] == "s3cret"
