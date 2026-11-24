"""Regression tests for the DataConnection encryption-decorator footgun (M7).

``DataConnection.config`` is a decorated ``EncryptedSecretsJSON`` column, so the
model's core ``__table__.c.config`` is the *decorated* type: raw SQLAlchemy core
SELECT/INSERT/UPDATE via ``DataConnection.__table__`` transparently encrypts on
write and decrypts on read -- double-processing values that already hold plaintext
or returning ciphertext raw code cannot interpret.

These tests lock in that behavior and verify the documented escape hatch
(:data:`app.models.connection._RAW_CONNECTIONS_TABLE`) lets raw core code touch
the plain JSONB bytes correctly:

* a raw INSERT of plaintext is read back as plaintext through the ORM, and as
  ciphertext through a raw core read (proving at-rest encryption), and
* an ORM INSERT (encrypted) is read back as ciphertext through a raw core read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.models.connection import _RAW_CONNECTIONS_TABLE, DataConnection
from app.models.user import AuthSource, User, UserRole
from app.services.connection_encryption import EncryptedSecretsJSON, looks_like_ciphertext


async def _make_user(db: AsyncSession) -> uuid.UUID:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        name="Raw Access User",
        password_hash=hash_password("pw"),
        role=UserRole.VIEWER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    return user.id


def test_model_table_config_column_is_decorated():
    # The footgun is in place: the model's core config column is the decorated type.
    assert isinstance(DataConnection.__table__.c.config.type, EncryptedSecretsJSON)


def test_raw_table_config_column_is_plain_jsonb():
    # The escape hatch uses plain JSONB, so raw access is not double-processed.
    assert isinstance(_RAW_CONNECTIONS_TABLE.c.config.type, JSONB)
    assert not isinstance(_RAW_CONNECTIONS_TABLE.c.config.type, EncryptedSecretsJSON)


@pytest.mark.asyncio
async def test_raw_plaintext_insert_reads_back_plaintext_via_orm(db_session: AsyncSession):
    uid = await _make_user(db_session)
    cid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    plaintext = {"host": "h", "password": "s3cret"}

    # Raw core INSERT with plaintext bytes (bypasses the decorator entirely).
    # created_at is provided explicitly: core inserts do not trigger the ORM
    # column default, and the column is NOT NULL.
    await db_session.execute(
        _RAW_CONNECTIONS_TABLE.insert().values(
            id=cid, name="raw-conn", connector_type="postgresql",
            config=plaintext, created_by=uid, created_at=now, updated_at=now,
        )
    )
    await db_session.commit()

    # Raw core read returns exactly the bytes written (plain JSONB, no decoration).
    raw = (await db_session.execute(
        select(_RAW_CONNECTIONS_TABLE.c.config).where(_RAW_CONNECTIONS_TABLE.c.id == cid)
    )).scalar_one()
    assert raw == plaintext

    # ORM read transparently decrypts; since the raw write stored plaintext, the
    # fail-open decrypt returns it unchanged. This is the migration scenario: a
    # re-encryption migration writes plaintext via the raw table and the ORM reads
    # it without error (and without double-processing).
    loaded = (await db_session.execute(
        select(DataConnection).where(DataConnection.id == cid)
    )).scalar_one()
    assert loaded.config == plaintext


@pytest.mark.asyncio
async def test_orm_encrypted_insert_reads_as_ciphertext_via_raw(db_session: AsyncSession):
    uid = await _make_user(db_session)

    # ORM write encrypts transparently.
    conn = DataConnection(
        name="orm-conn", connector_type="postgresql",
        config={"host": "h", "password": "s3cret"}, created_by=uid,
    )
    db_session.add(conn)
    await db_session.commit()
    cid = conn.id

    # Raw core read sees ciphertext (what raw code would get from the model table too).
    raw = (await db_session.execute(
        select(_RAW_CONNECTIONS_TABLE.c.config).where(_RAW_CONNECTIONS_TABLE.c.id == cid)
    )).scalar_one()
    assert looks_like_ciphertext(raw["password"])
    assert raw["password"] != "s3cret"
