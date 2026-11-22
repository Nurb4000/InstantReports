"""Tests for audit-log retention (feature-gap #5).

Verifies ``cleanup_old_audit_logs`` purges entries older than the configured
retention window while keeping recent ones, and honours a 0 (disabled) setting.

The cleanup functions open their own session via ``app.database.async_session_factory``
(which points at the real DATABASE_URL), so each test builds a throwaway PostgreSQL
engine on the backend test DB, creates the schema, and monkeypatches that factory
target to stay local. The schema is dropped on teardown so each test starts from an
empty audit_log (the assertions count exact rows). AuditLog's FK columns are all
nullable and these entries set none of them, so no parent rows are required.
"""

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
    AsyncSession,
)

import pytest_asyncio

from app.config import settings as app_settings
from app.database import Base
from app.models import AuditLog
from app.services.cleanup import cleanup_old_audit_logs
from tests.conftest import TEST_DATABASE_URL


def _audit(age_days: int, action: str = "login") -> AuditLog:
    """Return an audit entry ``age_days`` in the past."""
    return AuditLog(
        id=uuid.uuid4(),
        action=action,
        details={"note": "x"},
        executed_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )


@pytest_asyncio.fixture
async def audit_db(monkeypatch):
    """A throwaway PostgreSQL engine on the backend test DB, isolated per test.

    The schema is created on setup and dropped on teardown so each test starts from
    an empty audit_log (the assertions count exact rows). cleanup's factory is
    patched to this engine so it operates only on these local rows.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.cleanup.async_session_factory", factory)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(engine, ages):
    async with factory_for(engine)() as db:
        for age in ages:
            db.add(_audit(age))
        await db.commit()
    return len(ages)


async def _count(engine):
    async with factory_for(engine)() as db:
        result = await db.execute(select(AuditLog))
        return len(result.scalars().all())


def factory_for(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_purges_entries_older_than_retention(audit_db):
    """Entries older than AUDIT_RETENTION_DAYS are deleted; recent ones kept."""
    await _seed(audit_db, [400, 4000, 10])
    assert await _count(audit_db) == 3

    deleted = await cleanup_old_audit_logs(retention_days=365)

    assert deleted == 2, f"expected 2 purged, got {deleted}"
    assert await _count(audit_db) == 1


async def test_recent_entry_survives(audit_db):
    """The entry inside the retention window is retained."""
    await _seed(audit_db, [400, 10])

    await cleanup_old_audit_logs(retention_days=365)

    assert await _count(audit_db) == 1


async def test_zero_retention_disables_purge(audit_db, monkeypatch):
    """AUDIT_RETENTION_DAYS=0 disables purging entirely (returns 0)."""
    await _seed(audit_db, [400, 4000, 10])
    monkeypatch.setattr(app_settings, "AUDIT_RETENTION_DAYS", 0, raising=True)

    deleted = await cleanup_old_audit_logs()

    assert deleted == 0
    assert await _count(audit_db) == 3


async def test_uses_default_retention_when_no_arg(audit_db, monkeypatch):
    """With no explicit days, the settings default governs the cutoff."""
    await _seed(audit_db, [400, 4000, 10])
    monkeypatch.setattr(app_settings, "AUDIT_RETENTION_DAYS", 100, raising=True)

    deleted = await cleanup_old_audit_logs()

    assert deleted == 2


async def test_no_entries_is_noop(audit_db):
    """An empty audit log returns 0 without error."""
    await _seed(audit_db, [])
    deleted = await cleanup_old_audit_logs(retention_days=30)
    assert deleted == 0
