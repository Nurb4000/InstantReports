"""Integration tests for schedule create/update materializing Delivery rows.

Covers the P0.3 / H1 fix: the create/update schedule routes stored delivery
settings on the Schedule row but never created the normalized
Delivery/DeliveryRecipient rows the scheduler reads, so scheduled reports were
never delivered.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.connection import Delivery, DeliveryRecipient


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
async def test_create_schedule_materializes_delivery(auth_client, db_session):
    resp = await auth_client.post(
        "/admin/schedules",
        data={
            "report_id": str(uuid.uuid4()),
            "name": "nightly",
            "cron_expression": "0 6 * * *",
            "delivery_type": "email",
            "recipient_emails": "a@x.test,b@x.test",
        },
    )
    assert resp.status_code == 200, resp.text
    schedule_id = uuid.UUID(resp.json()["id"])

    deliveries = await _deliveries(db_session, schedule_id)
    assert len(deliveries) == 1
    assert deliveries[0].delivery_type == "email"
    recips = await _recipients(db_session, deliveries[0].id)
    assert {r.email for r in recips} == {"a@x.test", "b@x.test"}


@pytest.mark.asyncio
async def test_update_schedule_reconciles_delivery(auth_client, db_session):
    created = await auth_client.post(
        "/admin/schedules",
        data={
            "report_id": str(uuid.uuid4()),
            "name": "nightly",
            "delivery_type": "email",
            "recipient_emails": "a@x.test",
        },
    )
    assert created.status_code == 200, created.text
    schedule_id = uuid.UUID(created.json()["id"])

    updated = await auth_client.put(
        f"/admin/schedules/{schedule_id}",
        data={
            "delivery_type": "email",
            "recipient_emails": "c@x.test,d@x.test",
        },
    )
    assert updated.status_code == 200, updated.text

    deliveries = await _deliveries(db_session, schedule_id)
    assert len(deliveries) == 1
    recips = await _recipients(db_session, deliveries[0].id)
    assert {r.email for r in recips} == {"c@x.test", "d@x.test"}
