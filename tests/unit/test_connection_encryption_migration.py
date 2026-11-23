"""Migration tests for encryption-at-rest of connection credentials (H2).

Exercises the ``c2e4f6a8b0d2`` re-encryption migration against PostgreSQL:

* it re-encrypts plaintext ``DataConnection.config`` and rotation
  ``secret_value`` rows that predate the change,
* it is idempotent -- running ``upgrade()`` twice never double-encrypts a value,
* ``downgrade()`` decrypts back to plaintext.

Rows are seeded with raw core ``insert()`` on locally declared tables (bypassing
the ORM encryption decorator) so they start as true plaintext -- exactly what a
production DB looks like before the migration runs.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, DateTime, String, Text, insert, select, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import TEST_DATABASE_URL
from app.database import Base
from app.services.connection_encryption import (
    decrypt_config_secrets,
    decrypt_secret,
    looks_like_ciphertext,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_FILE = (
    _REPO_ROOT
    / "alembic"
    / "versions"
    / "c2e4f6a8b0d2_encrypt_connection_credentials_at_rest.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_enc_migration", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Locally declared tables (plain JSONB / Text) for raw byte access -- the ORM
# encryption decorator lives on the mapped model, not here. Types match the real
# schema so asyncpg encodes/decodes each column correctly.
_meta = __import__("sqlalchemy").MetaData()
_dc = Table(
    "data_connections",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(255)),
    Column("connector_type", String(50)),
    Column("config", JSONB()),
    Column("created_by", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)
_rot = Table(
    "connection_credential_rotations",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("connection_id", UUID(as_uuid=True)),
    Column("field_name", String(64)),
    Column("secret_hash", String(64)),
    Column("secret_value", Text()),
    Column("valid_until", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
)
_users = Table(
    "users",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", String(255)),
    Column("name", String(255)),
    Column("password_hash", String(255)),
    Column("role", String(50)),
    Column("auth_source", String(50)),
    Column("is_active", Boolean()),
    Column("mfa_enabled", Boolean()),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)


def _seed_plaintext(sync_conn, uid: uuid.UUID, cid: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    sync_conn.execute(insert(_users).values(
        id=uid, email=f"mig-{uid.hex[:8]}@example.com", name="Mig",
        password_hash="x", role="designer", auth_source="local",
        is_active=True, mfa_enabled=False, created_at=now, updated_at=now))
    sync_conn.execute(insert(_dc).values(
        id=cid, name="mig-conn", connector_type="postgresql",
        config={"host": "h", "password": "plain-secret"}, created_by=uid,
        created_at=now, updated_at=now))
    sync_conn.execute(insert(_rot).values(
        id=uuid.uuid4(), connection_id=cid, field_name="password",
        secret_hash="0" * 64, secret_value="rotated-plain",
        valid_until=datetime(2999, 1, 1, tzinfo=timezone.utc), created_at=now))


def _run_migration(sync_conn, fn):
    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx) as op:
        module.op = op
        fn(sync_conn)


@pytest.mark.asyncio
async def test_encrypt_migration_reencrypts_idempotent_and_downgrades():
    global module
    module = _load_migration()
    engine = create_async_engine(TEST_DATABASE_URL)
    uid, cid = uuid.uuid4(), uuid.uuid4()

    def _seed(sync_conn):
        Base.metadata.create_all(sync_conn)
        _seed_plaintext(sync_conn, uid, cid)

    def _upgrade(sync_conn):
        # Idempotent: a second pass must not re-encrypt an already-ciphertext row.
        module.upgrade()
        module.upgrade()

    def _assert_ciphertext(sync_conn):
        cfg = sync_conn.execute(
            select(_dc.c.config).where(_dc.c.id == cid)
        ).scalar_one()
        assert isinstance(cfg, dict)
        assert looks_like_ciphertext(cfg["password"])
        # Not double-encrypted: a single decrypt recovers the original plaintext.
        assert decrypt_config_secrets(cfg)["password"] == "plain-secret"

        sec = sync_conn.execute(
            select(_rot.c.secret_value).where(_rot.c.connection_id == cid)
        ).scalar_one()
        assert looks_like_ciphertext(sec)
        assert decrypt_secret(sec) == "rotated-plain"

    def _downgrade(sync_conn):
        module.downgrade()

    def _assert_plaintext(sync_conn):
        cfg = sync_conn.execute(
            select(_dc.c.config).where(_dc.c.id == cid)
        ).scalar_one()
        assert cfg["password"] == "plain-secret"

        sec = sync_conn.execute(
            select(_rot.c.secret_value).where(_rot.c.connection_id == cid)
        ).scalar_one()
        assert sec == "rotated-plain"

    try:
        async with engine.begin() as conn:
            await conn.run_sync(_seed)
        async with engine.connect() as conn:
            await conn.run_sync(lambda sc: _run_migration(sc, _upgrade))
            await conn.run_sync(lambda sc: _run_migration(sc, _assert_ciphertext))
            await conn.run_sync(lambda sc: _run_migration(sc, _downgrade))
            await conn.run_sync(lambda sc: _run_migration(sc, _assert_plaintext))
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
