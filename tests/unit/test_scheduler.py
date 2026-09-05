"""Tests for the runner scheduler live-reload reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.date import DateTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.connection import Schedule
from app.services.scheduler.engine import ReportScheduler


async def _make_schedule(db, name: str, cron_expression: str | None = "0 * * * *", **kwargs):
    attrs = {
        "report_id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "created_by": uuid.uuid4(),
        "name": name,
        "cron_expression": cron_expression,
        "run_at": kwargs.pop("run_at", None),
        "is_active": kwargs.pop("is_active", True),
    }
    attrs.update(kwargs)
    schedule = Schedule(**attrs)
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def test_sync_adds_active_schedule(db_session):
    scheduler = ReportScheduler("sqlite://")
    schedule = await _make_schedule(db_session, name="added")

    count = await scheduler.sync_schedules(db_session)

    assert count == 1
    job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
    assert str(schedule.id) in job_ids


async def test_sync_updates_schedule_edited_in_place(db_session):
    scheduler = ReportScheduler("sqlite://")
    schedule = await _make_schedule(db_session, name="edited", cron_expression="0 * * * *")
    assert await scheduler.sync_schedules(db_session) == 1
    assert scheduler.scheduler.get_job(str(schedule.id)) is not None

    # Re-edit the cron and re-sync: the job must stay registered (updated in place).
    schedule.cron_expression = "15 * * * *"
    await db_session.commit()
    assert await scheduler.sync_schedules(db_session) == 1
    assert scheduler.scheduler.get_job(str(schedule.id)) is not None


async def test_sync_removes_deactivated_schedule(db_session):
    scheduler = ReportScheduler("sqlite://")
    schedule = await _make_schedule(db_session, name="deactivated")
    await scheduler.sync_schedules(db_session)
    assert scheduler.scheduler.get_job(str(schedule.id)) is not None

    schedule.is_active = False
    await db_session.commit()
    await scheduler.sync_schedules(db_session)
    assert scheduler.scheduler.get_job(str(schedule.id)) is None


async def test_sync_removes_schedule_deleted_from_db(db_session):
    scheduler = ReportScheduler("sqlite://")
    schedule = await _make_schedule(db_session, name="deleted")
    await scheduler.sync_schedules(db_session)
    assert scheduler.scheduler.get_job(str(schedule.id)) is not None

    await db_session.delete(schedule)
    await db_session.commit()
    await scheduler.sync_schedules(db_session)
    assert scheduler.scheduler.get_job(str(schedule.id)) is None


async def test_sync_keeps_runner_cleanup_job(db_session):
    scheduler = ReportScheduler("sqlite://")
    # A one-shot job with the runner's cleanup id must never be reconciled away.
    scheduler.scheduler.add_job(
        lambda: None,
        id="cleanup_old_reports",
        trigger=DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(hours=1)),
    )
    await _make_schedule(db_session, name="present")
    await scheduler.sync_schedules(db_session)

    assert scheduler.scheduler.get_job("cleanup_old_reports") is not None


async def test_deliver_scheduled_loads_active_deliveries_and_recipients(db_session, monkeypatch):
    """_deliver_scheduled loads a schedule's ACTIVE deliveries and their recipients and passes
    them to runner.deliver_report. Inactive deliveries — and recipients attached to inactive
    deliveries — must be excluded."""
    import types

    from app.models.connection import Delivery, DeliveryRecipient
    from app.services.scheduler.engine import _deliver_scheduled

    schedule_id = uuid.uuid4()
    active = Delivery(
        id=uuid.uuid4(), schedule_id=schedule_id, delivery_type="webhook",
        config={"url": "http://x"}, is_active=True,
    )
    inactive = Delivery(
        id=uuid.uuid4(), schedule_id=schedule_id, delivery_type="email",
        config={}, is_active=False,
    )
    db_session.add_all([active, inactive])
    await db_session.commit()

    active_recip = DeliveryRecipient(
        id=uuid.uuid4(), delivery_id=active.id, email="a@example.com",
    )
    inactive_recip = DeliveryRecipient(
        id=uuid.uuid4(), delivery_id=inactive.id, email="i@example.com",
    )
    db_session.add_all([active_recip, inactive_recip])
    await db_session.commit()

    calls = {}

    async def fake_deliver(output, deliveries, recipients):
        calls["deliv"] = [d.id for d in deliveries]
        calls["recip"] = [r.id for r in recipients]
        return True

    monkeypatch.setattr("app.runner.deliver_report", fake_deliver)

    await _deliver_scheduled(types.SimpleNamespace(id=uuid.uuid4()), schedule_id, db_session)

    assert calls, "deliver_report was never called"
    assert calls["deliv"] == [active.id]          # only the active delivery
    assert calls["recip"] == [active_recip.id]    # only its recipient


async def test_execute_report_invokes_delivery(monkeypatch):
    """Regression: _execute_report must call _deliver_scheduled with the generated output.
    Previously execute_report()'s return value was discarded, so scheduled reports were
    generated and saved but never delivered."""
    import types

    from app.database import Base
    from app.models.connection import Schedule
    from app.services.scheduler.engine import ReportScheduler

    engine = create_async_engine("sqlite+aiosqlite:///./test.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # this engine owns its own tables
    monkeypatch.setattr("app.database.async_session_factory", factory)

    fake_output = types.SimpleNamespace(id=uuid.uuid4())
    called = {}

    async def fake_execute_report(schedule, db):
        return fake_output

    async def capturing_deliver(output, schedule_id, db):
        called["schedule_id"] = str(schedule_id)
        called["output"] = output

    monkeypatch.setattr("app.runner.execute_report", fake_execute_report)
    monkeypatch.setattr("app.services.scheduler.engine._deliver_scheduled", capturing_deliver)

    async with factory() as db:
        sched = Schedule(
            id=uuid.uuid4(), report_id=uuid.uuid4(), owner_id=uuid.uuid4(),
            created_by=uuid.uuid4(), name="x", is_active=True,
        )
        db.add(sched)
        await db.commit()
        job_id = str(sched.id)

    # Bypass __init__ (no scheduler side effects); _execute_report only needs self.
    scheduler = ReportScheduler.__new__(ReportScheduler)
    await scheduler._execute_report(job_id=job_id)

    assert called, "deliver was not invoked from _execute_report"
    assert called["output"] is fake_output
    assert called["schedule_id"] == job_id

    await engine.dispose()


async def test_execute_report_skips_delivery_when_no_output(monkeypatch):
    """If execute_report returns None (render/export failed), delivery must be skipped."""
    from app.database import Base
    from app.services.scheduler.engine import ReportScheduler

    engine = create_async_engine("sqlite+aiosqlite:///./test.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr("app.database.async_session_factory", factory)

    calls = []

    async def null_execute_report(schedule, db):
        return None

    async def recording_deliver(schedule_id, output, db):
        calls.append(output)

    monkeypatch.setattr("app.runner.execute_report", null_execute_report)
    monkeypatch.setattr(
        "app.services.scheduler.engine._deliver_scheduled", recording_deliver
    )

    scheduler = ReportScheduler.__new__(ReportScheduler)
    await scheduler._execute_report(job_id=str(uuid.uuid4()))

    assert calls == []
    await engine.dispose()


async def test_execute_report_attributes_output_to_schedule_owner(monkeypatch):
    """Regression: scheduled outputs record generated_by = schedule.owner_id so
    the portal can attribute executions to the owning user. Previously
    ReportOutput.generated_by was left NULL, so non-admin visibility depended
    solely on schedule ownership."""
    from app.database import Base
    from app.models.report import Report
    from app.runner import execute_report

    engine = create_async_engine("sqlite+aiosqlite:///./test.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Stub the heavy render/export core; we only care about output attribution.
    async def _fake_fetch(schedule, db, definition):
        return {}

    def _fake_export(rendered, fmt):
        return b"fake-csv-bytes"

    monkeypatch.setattr("app.runner._fetch_element_data", _fake_fetch)
    monkeypatch.setattr("app.services.exporters.export_report", _fake_export)

    owner = uuid.uuid4()
    async with factory() as db:
        report = Report(
            id=uuid.uuid4(), name="Attributed Report", created_by=owner,
            definition={"layout": {"sections": []}},
        )
        db.add(report)
        await db.commit()

        schedule = Schedule(
            id=uuid.uuid4(), report_id=report.id, owner_id=owner,
            created_by=owner, name="x", output_format="csv", is_active=True,
        )
        db.add(schedule)
        await db.commit()
        await db.refresh(schedule)

        output = await execute_report(schedule, db)

    assert output is not None, "execute_report should have produced an output"
    assert output.generated_by == owner, (
        f"generated_by={output.generated_by} expected {owner}"
    )

    await engine.dispose()
