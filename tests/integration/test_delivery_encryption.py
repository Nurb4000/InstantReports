"""Integration tests for encryption-at-rest of delivery credentials (H5).

These prove the storage layer only ever holds ciphertext for delivery secrets while
the application still sees plaintext:

* ``schedules.delivery_config`` and ``deliveries.config`` store SFTP/SMB ``password``
  and webhook ``secret`` as ciphertext (read via raw SQL, bypassing the ORM decorator),
* the ORM still returns plaintext so the scheduler / connectors keep working, and
* the schedule API response masks secrets (redact_delivery_config) so they are never
  echoed back to callers.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.connection import Delivery, Schedule
from app.models.report import Report
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional
from app.services.connection_encryption import looks_like_ciphertext


def _admin_user() -> User:
    return User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        name="Admin",
        password_hash=hash_password("pw"),
        role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


async def _make_schedule(db: AsyncSession, delivery_config: dict) -> Schedule:
    user = _admin_user()
    db.add(user)
    report = Report(name="R", created_by=user.id)
    db.add(report)
    await db.commit()
    schedule = Schedule(
        report_id=report.id,
        name=f"job-{uuid.uuid4().hex[:8]}",
        owner_id=user.id,
        created_by=user.id,
        delivery_type=delivery_config.get("type", "sftp"),
        delivery_config=delivery_config,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def _raw_schedule_config(db: AsyncSession, sid: uuid.UUID):
    """Read the stored ``delivery_config`` bypassing the ORM decorator (ciphertext)."""
    row = await db.execute(
        text("SELECT delivery_config FROM schedules WHERE id = :sid"), {"sid": sid}
    )
    return row.scalar_one()


async def _make_delivery(db: AsyncSession, schedule: Schedule, config: dict) -> Delivery:
    delivery = Delivery(
        schedule_id=schedule.id,
        delivery_type=config.get("type", "sftp"),
        config=config,
        is_active=True,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery


async def _raw_delivery_config(db: AsyncSession, did: uuid.UUID):
    row = await db.execute(
        text("SELECT config FROM deliveries WHERE id = :did"), {"did": did}
    )
    return row.scalar_one()


@pytest.mark.asyncio
async def test_delivery_config_stored_encrypted_but_read_as_plaintext(
    db_session: AsyncSession,
):
    schedule = await _make_schedule(
        db_session,
        {"type": "sftp", "host": "ftp.example.com", "username": "u",
         "password": "sftp-plain", "remote_path": "/"},
    )

    # Stored bytes are ciphertext -- the plaintext password is NOT on disk.
    raw = await _raw_schedule_config(db_session, schedule.id)
    assert raw["password"] != "sftp-plain"
    assert looks_like_ciphertext(raw["password"])
    # Non-secret metadata is stored verbatim.
    assert raw["host"] == "ftp.example.com"

    # The ORM still returns plaintext to the application (scheduler / connectors).
    reloaded = (
        await db_session.execute(
            select(Schedule).where(Schedule.id == schedule.id)
        )
    ).scalar_one()
    assert reloaded.delivery_config["password"] == "sftp-plain"
    assert reloaded.delivery_config["host"] == "ftp.example.com"

    # The normalized Delivery row (what the scheduler actually reads) is encrypted too.
    delivery = await _make_delivery(
        db_session, schedule,
        {"type": "webhook", "url": "https://x/hook", "secret": "wh-plain"},
    )
    raw_del = await _raw_delivery_config(db_session, delivery.id)
    assert looks_like_ciphertext(raw_del["secret"])
    assert raw_del["secret"] != "wh-plain"
    reloaded_del = (
        await db_session.execute(
            select(Delivery).where(Delivery.id == delivery.id)
        )
    ).scalar_one()
    assert reloaded_del.config["secret"] == "wh-plain"


@pytest.mark.asyncio
async def test_api_redacts_delivery_secret_while_db_stores_ciphertext(
    client, db_session: AsyncSession,
):
    fastapi_app.dependency_overrides[get_current_user_optional] = _admin_user
    try:
        schedule = await _make_schedule(
            db_session,
            {"type": "sftp", "host": "ftp.example.com", "username": "u",
             "password": "sftp-plain", "remote_path": "/"},
        )

        resp = await client.get(f"/admin/api/schedules/{schedule.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # API masks the secret...
        assert body["delivery_config"]["password"] == "***REDACTED***"
        assert "sftp-plain" not in resp.text
        # ...and the DB holds ciphertext, not the plaintext password.
        raw = await _raw_schedule_config(db_session, schedule.id)
        assert looks_like_ciphertext(raw["password"])
        assert raw["password"] != "sftp-plain"
    finally:
        fastapi_app.dependency_overrides.clear()
