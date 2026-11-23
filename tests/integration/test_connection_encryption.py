"""Integration tests for encryption-at-rest of connection credentials (H2).

These prove the storage layer only ever holds ciphertext while the application
still sees plaintext:

* a connection's secret is stored as ciphertext in ``data_connections.config``
  (read via raw SQL, bypassing the ORM decorator),
* the ORM still returns plaintext so connectors / resolution keep working,
* the API response masks secrets, and
* updates re-encrypt.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.connection import DataConnection
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional
from app.services.connection_encryption import looks_like_ciphertext
from app.services.connection_credentials import resolve_connection_config


def _designer_user() -> User:
    return User(
        id=uuid.uuid4(),
        email=f"designer-{uuid.uuid4().hex[:8]}@example.com",
        name="Designer",
        password_hash=hash_password("pw"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


async def _make_connection(db: AsyncSession, config: dict, role=UserRole.DESIGNER) -> DataConnection:
    user = _designer_user()
    db.add(user)
    conn = DataConnection(
        name=f"conn-{uuid.uuid4().hex[:8]}",
        connector_type="postgresql",
        config=config,
        created_by=user.id,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def _raw_config(db: AsyncSession, name: str):
    """Read the stored ``config`` bypassing the ORM decorator (sees ciphertext)."""
    row = await db.execute(
        text("SELECT config FROM data_connections WHERE name = :nm"), {"nm": name}
    )
    raw = row.scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.asyncio
async def test_config_stored_encrypted_but_read_as_plaintext(db_session: AsyncSession):
    conn = await _make_connection(
        db_session, {"host": "db.local", "database": "sales", "password": "s3cret"}
    )

    # Stored bytes are ciphertext -- the plaintext password is NOT on disk.
    raw = await _raw_config(db_session, conn.name)
    assert raw["password"] != "s3cret"
    assert looks_like_ciphertext(raw["password"])
    # Non-secret metadata is stored verbatim.
    assert raw["host"] == "db.local"

    # The ORM still returns plaintext to the application.
    reloaded = (
        await db_session.execute(
            select(DataConnection).where(DataConnection.id == conn.id)
        )
    ).scalar_one()
    assert reloaded.config["password"] == "s3cret"
    assert reloaded.config["host"] == "db.local"


@pytest.mark.asyncio
async def test_api_get_masks_secret_while_db_stores_ciphertext(
    client, db_session: AsyncSession
):
    fastapi_app.dependency_overrides[get_current_user_optional] = _designer_user
    try:
        conn = await _make_connection(
            db_session, {"host": "h", "auth_token": "bearer-abc"}
        )

        resp = await client.get(f"/datasources/{conn.id}")
        assert resp.status_code == 200
        body = resp.json()
        # API masks the secret...
        assert body["config"]["auth_token"] == "***"
        assert "bearer-abc" not in resp.text
        # ...and the DB holds ciphertext, not the plaintext token.
        raw = await _raw_config(db_session, conn.name)
        assert looks_like_ciphertext(raw["auth_token"])
        assert raw["auth_token"] != "bearer-abc"
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_resolve_connection_config_returns_plaintext_after_encryption(
    db_session: AsyncSession,
):
    """Encryption must not break credential resolution used by connectors."""
    conn = await _make_connection(
        db_session, {"host": "h", "password": "resolve-me-123"}
    )
    resolved = await resolve_connection_config(db_session, conn)
    assert resolved["password"] == "resolve-me-123"
    assert resolved["host"] == "h"


@pytest.mark.asyncio
async def test_update_connection_reencrypts_new_secret(db_session: AsyncSession):
    conn = await _make_connection(db_session, {"host": "h", "password": "old-pw"})

    conn.config = {"host": "h", "password": "new-pw-456"}
    await db_session.commit()

    raw = await _raw_config(db_session, conn.name)
    assert raw["password"] != "new-pw-456"
    assert looks_like_ciphertext(raw["password"])
    # Still readable as plaintext through the ORM.
    model = (
        await db_session.execute(
            select(DataConnection).where(DataConnection.id == conn.id)
        )
    ).scalar_one()
    assert model.config["password"] == "new-pw-456"


@pytest.mark.asyncio
async def test_rotation_secret_value_stored_encrypted(db_session: AsyncSession):
    from app.services.connection_credentials import rotate_connection_secret

    user = _designer_user()
    db_session.add(user)
    conn = DataConnection(
        name=f"rot-{uuid.uuid4().hex[:8]}",
        connector_type="postgresql",
        config={"host": "h", "password": "first"},
        created_by=user.id,
    )
    db_session.add(conn)
    await db_session.commit()

    await rotate_connection_secret(
        db_session, conn.id, user.id, "password", "second", grace_period_days=7
    )

    row = (
        await db_session.execute(
            text(
                "SELECT secret_value FROM connection_credential_rotations "
                "WHERE connection_id = :cid AND field_name = 'password'"
            ),
            {"cid": conn.id},
        )
    ).scalar_one()
    # EncryptedText stores a bare ciphertext string (not JSON).
    assert looks_like_ciphertext(row)
    # The retired plaintext value is encrypted at rest, not sitting in cleartext.
    assert row != "first"
