"""Tests for the runner scheduler live-reload reconciliation."""

from __future__ import annotations

import types
import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.date import DateTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.connection import Schedule
from app.models.report import Report
from app.models.user import User, AuthSource, UserRole
from app.services.scheduler.engine import ReportScheduler


async def _make_schedule(db, name: str, cron_expression: str | None = "0 * * * *", **kwargs):
    # Schedule has three NOT NULL foreign keys (report_id -> reports, owner_id and
    # created_by -> users). Create real parent rows so the constraints hold on
    # PostgreSQL, which enforces them (SQLite silently ignores FK violations -- that
    # is why these fixtures were valid there but fail here).
    user = User(
        id=uuid.uuid4(),
        email=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}@test",
        name=name,
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    report = Report(
        id=uuid.uuid4(),
        name=f"Report for {name}",
        created_by=user.id,
    )
    db.add(report)
    await db.flush()

    attrs = {
        "report_id": report.id,
        "owner_id": user.id,
        "created_by": user.id,
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
    # Real parent rows so the deliveries' schedule_id FK (and the schedule's own
    # report_id/owner_id/created_by FKs) hold on PostgreSQL.
    user = User(
        id=uuid.uuid4(), email=f"deliver-{uuid.uuid4().hex[:8]}@test",
        name="deliver", role=UserRole.DESIGNER, auth_source=AuthSource.LOCAL, is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    report = Report(id=uuid.uuid4(), name="deliver report", created_by=user.id)
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        Schedule(
            id=schedule_id, report_id=report.id, owner_id=user.id,
            created_by=user.id, name="deliver", is_active=True,
        )
    )
    await db_session.commit()

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


async def test_execute_report_sends_failure_notification_on_error(monkeypatch):
    """    Regression: a scheduled run that raises must trigger a failure-notification
    email. ``send_failure_notification`` existed in app.services.cleanup but was
    never wired into the scheduler, so failures were logged and nobody told."""
    from app.database import Base
    from app.models.connection import Schedule
    from app.services.scheduler.engine import ReportScheduler

    engine = create_async_engine("sqlite+aiosqlite:///./test.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr("app.database.async_session_factory", factory)

    async def boom(schedule, db):
        raise ValueError("export blew up")

    notifications = []

    async def fake_send_email(**kwargs):
        notifications.append(kwargs)
        return True

    monkeypatch.setattr("app.runner.execute_report", boom)
    monkeypatch.setattr("app.services.cleanup.send_email", fake_send_email)

    async with factory() as db:
        sched = Schedule(
            id=uuid.uuid4(), report_id=uuid.uuid4(), owner_id=uuid.uuid4(),
            created_by=uuid.uuid4(), name="failing", is_active=True,
        )
        db.add(sched)
        await db.commit()
        job_id = str(sched.id)

    scheduler = ReportScheduler.__new__(ReportScheduler)
    await scheduler._execute_report(job_id=job_id)

    assert notifications, "no failure-notification email was sent on schedule error"
    assert "failing" in notifications[0]["subject"]
    assert "export blew up" in notifications[0]["body"]

    await engine.dispose()


class TestScheduleDatasourceAuthorization:
    """D5: the scheduler runs reports server-side with each datasource's stored
    credentials, so before executing we verify the schedule owner is authorized
    to use every referenced connection (created it or is admin). Unauthorized,
    missing, or inactive datasources cause the run to be skipped.
    """

    async def _run(self, monkeypatch, schedule, report=None, connections=(), users=()):
        """Persist entities, stub execute_report, and run the scheduler job.

        Returns True if execute_report was invoked (run proceeded), False if it
        was skipped by the D5 authorization check.
        """
        from app.models.connection import DataConnection
        from app.models.report import Report
        from app.models.user import User
        from app.services.scheduler.engine import ReportScheduler

        engine = create_async_engine("sqlite+aiosqlite:///./test.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(__import__("app.database", fromlist=["Base"]).Base.metadata.create_all)

        called = {"run": False}

        async def fake_execute_report(schedule, db):
            called["run"] = True
            return types.SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr("app.runner.execute_report", fake_execute_report)
        monkeypatch.setattr("app.database.async_session_factory", factory)

        async with factory() as db:
            for user in users:
                db.add(user)
            for conn in connections:
                db.add(conn)
            if report is not None:
                db.add(report)
            db.add(schedule)
            await db.commit()

            scheduler = ReportScheduler.__new__(ReportScheduler)
            await scheduler._execute_report(job_id=str(schedule.id))

        await engine.dispose()
        return called["run"]

    async def _user(self, monkeypatch, role, uid=None):
        from app.models.user import User, UserRole

        uid = uid or uuid.uuid4()
        return User(id=uid, email=f"{uid}@example.com", name="U", password_hash="x", role=role)

    async def _conn(self, created_by, conn_id=None):
        from app.models.connection import DataConnection

        return DataConnection(
            id=conn_id or uuid.uuid4(),
            name="c",
            connector_type="postgresql",
            config={"host": "h"},
            created_by=created_by,
        )

    async def _report(self, owner, connection_id):
        from app.models.report import Report

        return Report(
            id=uuid.uuid4(),
            name="r",
            created_by=owner,
            definition={
                "data_sources": [{"connection_id": str(connection_id)}],
                "layout": {"sections": []},
            },
        )

    async def _schedule(self, owner, report_id):
        return Schedule(
            id=uuid.uuid4(),
            report_id=report_id,
            owner_id=owner,
            created_by=owner,
            name="s",
            cron_expression="0 * * * *",
            is_active=True,
        )

    async def test_owner_may_run_own_datasource(self, monkeypatch):
        """A designer that created the referenced connection may run it."""
        owner = await self._user(monkeypatch, UserRole.DESIGNER)
        conn = await self._conn(owner.id)
        report = await self._report(owner.id, conn.id)
        schedule = await self._schedule(owner.id, report.id)

        ran = await self._run(monkeypatch, schedule, report, [conn], [owner])
        assert ran is True

    async def test_admin_may_run_any_datasource(self, monkeypatch):
        """An admin owner may run a report against a connection they did not create."""
        owner = await self._user(monkeypatch, UserRole.ADMIN)
        other = await self._user(monkeypatch, UserRole.DESIGNER, uid=uuid.uuid4())
        conn = await self._conn(other.id)
        report = await self._report(owner.id, conn.id)
        schedule = await self._schedule(owner.id, report.id)

        ran = await self._run(monkeypatch, schedule, report, [conn], [owner, other])
        assert ran is True

    async def test_designer_cannot_run_other_users_datasource(self, monkeypatch):
        """A designer cannot schedule a report against another user's connection."""
        owner = await self._user(monkeypatch, UserRole.DESIGNER)
        other = await self._user(monkeypatch, UserRole.DESIGNER, uid=uuid.uuid4())
        conn = await self._conn(other.id)
        report = await self._report(owner.id, conn.id)
        schedule = await self._schedule(owner.id, report.id)

        ran = await self._run(monkeypatch, schedule, report, [conn], [owner, other])
        assert ran is False

    async def test_skips_when_datasource_missing(self, monkeypatch):
        """A referenced connection_id with no matching row skips the run."""
        owner = await self._user(monkeypatch, UserRole.DESIGNER)
        report = await self._report(owner.id, uuid.uuid4())  # no such connection row
        schedule = await self._schedule(owner.id, report.id)

        ran = await self._run(monkeypatch, schedule, report, [], [owner])
        assert ran is False

    async def test_no_datasources_alows_run(self, monkeypatch):
        """A report referencing no connections (static) always runs."""
        owner = await self._user(monkeypatch, UserRole.DESIGNER)
        report = Report(
            id=uuid.uuid4(), name="r", created_by=owner.id,
            definition={"layout": {"sections": []}},
        )
        schedule = await self._schedule(owner.id, report.id)

        ran = await self._run(monkeypatch, schedule, report, [], [owner])
        assert ran is True
