"""Tests for sync_schedule_deliveries (P0.3 / H1)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.connection import Delivery, DeliveryRecipient, Schedule
from app.services.delivery.config import sync_schedule_deliveries


async def _schedule(db, **overrides) -> Schedule:
    fields = {
        "report_id": uuid.uuid4(),
        "name": "Sched",
        "owner_id": uuid.uuid4(),
        "created_by": uuid.uuid4(),
    }
    fields.update(overrides)
    sched = Schedule(**fields)
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return sched


async def _deliveries(db, schedule_id):
    result = await db.execute(
        select(Delivery).where(Delivery.schedule_id == schedule_id)
    )
    return list(result.scalars().all())


async def _recipients(db, delivery_id):
    result = await db.execute(
        select(DeliveryRecipient).where(
            DeliveryRecipient.delivery_id == delivery_id
        )
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_email_schedule_creates_delivery_and_recipients(db_session):
    sched = await _schedule(
        db_session,
        delivery_type="email",
        delivery_config={"type": "email", "emails": ["a@x.test", "b@x.test"]},
        recipient_emails="a@x.test, b@x.test",
    )

    await sync_schedule_deliveries(db_session, sched)
    await db_session.commit()

    deliveries = await _deliveries(db_session, sched.id)
    assert len(deliveries) == 1
    assert deliveries[0].delivery_type == "email"
    assert deliveries[0].is_active is True

    recips = await _recipients(db_session, deliveries[0].id)
    assert {r.email for r in recips} == {"a@x.test", "b@x.test"}


@pytest.mark.asyncio
async def test_sftp_schedule_has_no_recipients(db_session):
    sched = await _schedule(
        db_session,
        delivery_type="sftp",
        delivery_config={"type": "sftp", "host": "h", "remote_path": "/out"},
    )

    await sync_schedule_deliveries(db_session, sched)
    await db_session.commit()

    deliveries = await _deliveries(db_session, sched.id)
    assert len(deliveries) == 1
    assert deliveries[0].delivery_type == "sftp"
    assert await _recipients(db_session, deliveries[0].id) == []


@pytest.mark.asyncio
async def test_resync_replaces_rows_without_duplicating(db_session):
    sched = await _schedule(
        db_session,
        delivery_type="email",
        delivery_config={"type": "email", "emails": ["a@x.test"]},
        recipient_emails="a@x.test",
    )
    await sync_schedule_deliveries(db_session, sched)
    await db_session.commit()

    sched.recipient_emails = "c@x.test,d@x.test"
    sched.delivery_config = {"type": "email", "emails": ["c@x.test", "d@x.test"]}
    await sync_schedule_deliveries(db_session, sched)
    await db_session.commit()

    deliveries = await _deliveries(db_session, sched.id)
    assert len(deliveries) == 1
    recips = await _recipients(db_session, deliveries[0].id)
    assert {r.email for r in recips} == {"c@x.test", "d@x.test"}


@pytest.mark.asyncio
async def test_schedule_without_config_creates_nothing(db_session):
    sched = await _schedule(db_session, delivery_type="", delivery_config=None)

    await sync_schedule_deliveries(db_session, sched)
    await db_session.commit()

    assert await _deliveries(db_session, sched.id) == []
