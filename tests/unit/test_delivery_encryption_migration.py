"""Migration tests for encryption-at-rest of delivery credentials (H5).

Exercises the ``f7a2c9d4e8b1`` re-encryption migration against PostgreSQL:

* it re-encrypts plaintext ``schedules.delivery_config`` and ``deliveries.config``
  rows that predate the change (SFTP/SMB ``password``, webhook ``secret``),
* it is idempotent -- running ``upgrade()`` twice never double-encrypts a value,
* ``downgrade()`` decrypts back to plaintext.

Rows are seeded with raw core ``insert()`` on locally declared plain-JSONB tables
(bypassing the ORM encryption decorator) so they start as true plaintext -- exactly
what a production DB looks like before the migration runs. Mirrors the H2 connection
migration test's seeding approach.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import TEST_DATABASE_URL
from app.database import Base
from app.services.connection_encryption import (
    decrypt_config_secrets,
    looks_like_ciphertext,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_FILE = (
    _REPO_ROOT
    / "alembic"
    / "versions"
    / "f7a2c9d4e8b1_encrypt_delivery_credentials_at_rest.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Locally declared plain-JSONB tables for raw byte access -- the ORM encryption
# decorator lives on the mapped model, not here. Every NOT NULL column is provided
# on insert so the rows are true plaintext regardless of ORM-level defaults.
_meta = __import__("sqlalchemy").MetaData()

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

_reports = Table(
    "reports",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(255)),
    Column("description", Text()),
    Column("definition", JSONB()),
    Column("is_active", Boolean()),
    Column("created_by", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

_schedule = Table(
    "schedules",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("report_id", UUID(as_uuid=True)),
    Column("name", String(255)),
    Column("cron_expression", String(100)),
    Column("run_at", DateTime(timezone=True)),
    Column("timezone", String(100)),
    Column("is_active", Boolean()),
    Column("parameters", JSONB()),
    Column("output_format", String(20)),
    Column("delivery_type", String(50)),
    Column("delivery_config", JSONB()),
    Column("recipient_emails", Text()),
    Column("owner_id", UUID(as_uuid=True)),
    Column("created_by", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True)),
)

_delivery = Table(
    "deliveries",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("schedule_id", UUID(as_uuid=True)),
    Column("delivery_type", String(50)),
    Column("config", JSONB()),
    Column("is_active", Boolean()),
    Column("created_at", DateTime(timezone=True)),
)


def _seed_plaintext(sync_conn, uid: uuid.UUID, rid: uuid.UUID, sid: uuid.UUID, did: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    # Parent rows via raw inserts (all NOT NULL columns provided explicitly).
    sync_conn.execute(insert(_users).values(
        id=uid, email=f"mig-{uid.hex[:8]}@example.com", name="Mig",
        password_hash="x", role="admin", auth_source="local",
        is_active=True, mfa_enabled=False, created_at=now, updated_at=now))
    sync_conn.execute(insert(_reports).values(
        id=rid, name="mig-report", description=None,
        definition={"type": "table"}, is_active=True, created_by=uid,
        created_at=now, updated_at=now))
    # Plain-text delivery secrets -- exactly what predates the migration.
    sync_conn.execute(insert(_schedule).values(
        id=sid, report_id=rid, name="sftp-job", timezone="UTC",
        is_active=True, delivery_type="sftp", created_by=uid, owner_id=uid,
        created_at=now,
        delivery_config={"type": "sftp", "host": "ftp.example.com",
                         "username": "u", "password": "sftp-plain",
                         "remote_path": "/"}))
    sync_conn.execute(insert(_delivery).values(
        id=did, schedule_id=sid, delivery_type="sftp", is_active=True,
        created_at=now,
        config={"type": "webhook", "url": "https://x/hook", "secret": "wh-plain"}))


def _run_migration(sync_conn, fn):
    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx) as op:
        module.op = op
        fn(sync_conn)


@pytest.mark.asyncio
async def test_delivery_migration_reencrypts_idempotent_and_downgrades():
    global module
    module = _load_migration()
    engine = create_async_engine(TEST_DATABASE_URL)
    uid, rid, sid, did = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def _seed(sync_conn):
        Base.metadata.create_all(sync_conn)
        _seed_plaintext(sync_conn, uid, rid, sid, did)

    def _upgrade(sync_conn):
        # Idempotent: a second pass must not re-encrypt an already-ciphertext row.
        module.upgrade()
        module.upgrade()

    def _assert_ciphertext(sync_conn):
        scfg = sync_conn.execute(
            select(_schedule.c.delivery_config).where(_schedule.c.id == sid)
        ).scalar_one()
        assert isinstance(scfg, dict)
        assert looks_like_ciphertext(scfg["password"])
        # Not double-encrypted: a single decrypt recovers the original plaintext.
        assert decrypt_config_secrets(scfg)["password"] == "sftp-plain"
        assert scfg["host"] == "ftp.example.com"  # non-secret verbatim

        dcfg = sync_conn.execute(
            select(_delivery.c.config).where(_delivery.c.id == did)
        ).scalar_one()
        assert looks_like_ciphertext(dcfg["secret"])
        assert decrypt_config_secrets(dcfg)["secret"] == "wh-plain"
        assert decrypt_config_secrets(dcfg)["url"] == "https://x/hook"

    def _downgrade(sync_conn):
        module.downgrade()

    def _assert_plaintext(sync_conn):
        scfg = sync_conn.execute(
            select(_schedule.c.delivery_config).where(_schedule.c.id == sid)
        ).scalar_one()
        assert scfg["password"] == "sftp-plain"
        dcfg = sync_conn.execute(
            select(_delivery.c.config).where(_delivery.c.id == did)
        ).scalar_one()
        assert dcfg["secret"] == "wh-plain"

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
