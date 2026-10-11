"""Integration tests for schedule ownership scoping (H4).

Covers the authorization fix: schedule read/update previously authorized on
role alone, so any designer could read or edit any other user's schedule
(recipient_emails, delivery credentials, owner reassignment). Schedules are now
scoped to the owner/creator, with admins retaining oversight access.
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


async def _seed_schedule(db, report, owner) -> Schedule:
    schedule = Schedule(
        report_id=report.id,
        name="owned-job",
        owner_id=owner.id,
        created_by=owner.id,
        delivery_type="email",
        recipient_emails="owner@example.com",
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@pytest.mark.asyncio
async def test_designer_cannot_access_others_schedule(client, db_session):
    owner = _user(UserRole.DESIGNER, "sowner@x.test")
    other = _user(UserRole.DESIGNER, "sother@x.test")
    for u in (owner, other):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="R", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()
    schedule = await _seed_schedule(db_session, report, owner)

    _override_user(other)

    assert (
        await client.get(f"/admin/schedules/{schedule.id}")
    ).status_code == 403
    assert (
        await client.get(f"/admin/api/schedules/{schedule.id}")
    ).status_code == 403
    updated = await client.put(
        f"/admin/schedules/{schedule.id}",
        data={"name": "hijacked", "recipient_emails": "attacker@example.com"},
    )
    assert updated.status_code == 403

    listing = await client.get("/admin/api/schedules")
    assert listing.status_code == 200
    assert schedule.id.hex not in listing.text


@pytest.mark.asyncio
async def test_owner_and_admin_can_access_schedule(client, db_session):
    owner = _user(UserRole.DESIGNER, "sowner2@x.test")
    admin = _user(UserRole.ADMIN, "sadmin@x.test")
    for u in (owner, admin):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="R", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()
    schedule = await _seed_schedule(db_session, report, owner)

    _override_user(owner)
    assert (await client.get(f"/admin/api/schedules/{schedule.id}")).status_code == 200

    _override_user(admin)
    assert (await client.get(f"/admin/api/schedules/{schedule.id}")).status_code == 200


@pytest.mark.asyncio
async def test_non_admin_cannot_reassign_schedule_owner(client, db_session):
    owner = _user(UserRole.DESIGNER, "sowner3@x.test")
    victim = _user(UserRole.DESIGNER, "svictim@x.test")
    for u in (owner, victim):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="R", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()
    schedule = await _seed_schedule(db_session, report, owner)

    _override_user(owner)
    resp = await client.put(
        f"/admin/schedules/{schedule.id}",
        data={"owner_id": str(victim.id)},
    )
    assert resp.status_code == 403
