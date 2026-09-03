"""Tests for the runner scheduler live-reload reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.date import DateTrigger

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
