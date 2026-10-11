"""Tests for report-route authorization scoping (H4 IDOR).

Covers the fix: ``edit_report_page``, ``update_report``, and ``export_report``
previously authorized on role alone, so any designer could view, overwrite, or
export any other designer's report by UUID. They now follow the same
ownership model as the list/version endpoints (admin OR created_by).
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.report import Report
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional


def _make_user(role: UserRole, email: str | None = None) -> User:
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


async def _seed_report(db, owner) -> Report:
    report = Report(
        name="Owned Report",
        definition={"layout": {"sections": []}, "data_sources": [], "parameters": []},
        created_by=owner.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@pytest.mark.asyncio
async def test_non_owner_designer_cannot_read_update_or_export(client, db_session):
    owner = _make_user(UserRole.DESIGNER, "rowner@x.test")
    intruder = _make_user(UserRole.DESIGNER, "rintruder@x.test")
    for u in (owner, intruder):
        db_session.add(u)
    await db_session.commit()
    report = await _seed_report(db_session, owner)

    _override_user(intruder)

    assert (await client.get(f"/designer/reports/{report.id}")).status_code == 403
    updated = await client.post(
        f"/designer/reports/{report.id}",
        data={"name": "hijacked"},
    )
    assert updated.status_code == 403
    assert (await client.get(f"/designer/reports/{report.id}/export")).status_code == 403


@pytest.mark.asyncio
async def test_owner_and_admin_can_access_report(client, db_session):
    owner = _make_user(UserRole.DESIGNER, "rowner2@x.test")
    admin = _make_user(UserRole.ADMIN, "radmin@x.test")
    for u in (owner, admin):
        db_session.add(u)
    await db_session.commit()
    report = await _seed_report(db_session, owner)

    _override_user(owner)
    assert (await client.get(f"/designer/reports/{report.id}")).status_code == 200
    assert (await client.get(f"/designer/reports/{report.id}/export")).status_code == 200

    _override_user(admin)
    assert (await client.get(f"/designer/reports/{report.id}")).status_code == 200
