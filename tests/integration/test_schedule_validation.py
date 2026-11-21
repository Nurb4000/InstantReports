"""Integration tests for schedule input validation (Gap #8).

Covers the create/update schedule routes rejecting invalid cron expressions and
IANA timezones with HTTP 400 before persistence, so a typo fails at the API with
a clear message instead of breaking trigger build deep in the scheduler sync
loop. Validation reuses the exact resolvers APScheduler runs at runtime
(CronTrigger.from_crontab + pytz), so accepted/rejected sets match production.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.connection import Schedule
from app.models.report import Report
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional


def _user(role: UserRole, email: str | None = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=email or f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
        name=role.value,
        password_hash=hash_password("pw"),
        role=role,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


def _override_user(user: User) -> None:
    async def _dep():
        return user

    fastapi_app.dependency_overrides[get_current_user_optional] = _dep


async def _seed_report(db, owner) -> uuid.UUID:
    report = Report(name="R", created_by=owner.id)
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report.id


async def _seed_schedule(db, report_id, owner) -> Schedule:
    schedule = Schedule(
        report_id=report_id,
        name="owned-job",
        owner_id=owner.id,
        created_by=owner.id,
        delivery_type="email",
        recipient_emails="owner@example.com",
        cron_expression="0 6 * * *",
        timezone="UTC",
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@pytest.mark.asyncio
async def test_create_schedule_invalid_cron_returns_400(client, db_session):
    owner = _user(UserRole.ADMIN, "cowner@x.test")
    db_session.add(owner)
    await db_session.commit()
    report_id = await _seed_report(db_session, owner)

    _override_user(owner)
    resp = await client.post(
        "/admin/schedules",
        data={
            "report_id": str(report_id),
            "name": "bad-cron",
            "cron_expression": "99 * * * *",
            "timezone": "UTC",
        },
    )
    # APScheduler's message wording varies by version ("Error validating
    # expression..." / "Wrong number of fields..."), so assert on the 400
    # contract and that a human-readable detail is returned.
    assert resp.status_code == 400
    assert "detail" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_schedule_invalid_timezone_returns_400(client, db_session):
    owner = _user(UserRole.ADMIN, "cowner2@x.test")
    db_session.add(owner)
    await db_session.commit()
    report_id = await _seed_report(db_session, owner)

    _override_user(owner)
    resp = await client.post(
        "/admin/schedules",
        data={
            "report_id": str(report_id),
            "name": "bad-tz",
            "cron_expression": "0 6 * * *",
            "timezone": "America/BadZone",
        },
    )
    assert resp.status_code == 400
    assert "timezone" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_schedule_valid_values_accepted(client, db_session):
    owner = _user(UserRole.ADMIN, "cowner3@x.test")
    db_session.add(owner)
    await db_session.commit()
    report_id = await _seed_report(db_session, owner)

    _override_user(owner)
    resp = await client.post(
        "/admin/schedules",
        data={
            "report_id": str(report_id),
            "name": "good-schedule",
            "cron_expression": "0 6 * * *",
            "timezone": "America/New_York",
            "delivery_type": "email",
            "recipient_emails": "a@x.test",
        },
    )
    assert resp.status_code == 200, resp.text
    schedule_id = uuid.UUID(resp.json()["id"])

    from sqlalchemy import select as sa_select

    row = (
        await db_session.execute(
            sa_select(Schedule).where(Schedule.id == schedule_id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.cron_expression == "0 6 * * *"
    assert row.timezone == "America/New_York"


@pytest.mark.asyncio
async def test_update_schedule_invalid_cron_returns_400(client, db_session):
    owner = _user(UserRole.ADMIN, "uowner@x.test")
    db_session.add(owner)
    await db_session.commit()
    report_id = await _seed_report(db_session, owner)
    schedule = await _seed_schedule(db_session, report_id, owner)

    _override_user(owner)
    resp = await client.put(
        f"/admin/schedules/{schedule.id}",
        data={"cron_expression": "not a cron"},
    )
    # APScheduler's message wording varies by version; assert on the 400
    # contract and that a human-readable detail is returned.
    assert resp.status_code == 400
    assert "detail" in resp.text.lower()


@pytest.mark.asyncio
async def test_update_schedule_invalid_timezone_returns_400(client, db_session):
    owner = _user(UserRole.ADMIN, "uowner2@x.test")
    db_session.add(owner)
    await db_session.commit()
    report_id = await _seed_report(db_session, owner)
    schedule = await _seed_schedule(db_session, report_id, owner)

    _override_user(owner)
    resp = await client.put(
        f"/admin/schedules/{schedule.id}",
        data={"timezone": "Mars/Phobos"},
    )
    assert resp.status_code == 400
    assert "timezone" in resp.text.lower()


@pytest.mark.asyncio
async def test_update_schedule_valid_values_accepted(client, db_session):
    owner = _user(UserRole.ADMIN, "uowner3@x.test")
    db_session.add(owner)
    await db_session.commit()
    report_id = await _seed_report(db_session, owner)
    schedule = await _seed_schedule(db_session, report_id, owner)

    _override_user(owner)
    resp = await client.put(
        f"/admin/schedules/{schedule.id}",
        data={"cron_expression": "*/5 * * * *", "timezone": "Europe/London"},
    )
    assert resp.status_code == 200, resp.text

    from sqlalchemy import select as sa_select

    row = (
        await db_session.execute(
            sa_select(Schedule).where(Schedule.id == schedule.id)
        )
    ).scalar_one_or_none()
    assert row.cron_expression == "*/5 * * * *"
    assert row.timezone == "Europe/London"
