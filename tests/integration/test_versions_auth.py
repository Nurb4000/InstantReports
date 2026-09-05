"""Tests for versions-route authorization scoping.

Covers the round-7b fix: version endpoints must respect the report-visibility
model documented in designer.list_reports — admins may touch any report, but
other roles are scoped to reports they created. Before the fix these endpoints
only checked authentication, so any logged-in user could read or mutate another
team's version history and past definitions.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.report import Report
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional


def _make_user(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
        name=role.value,
        password_hash=hash_password("pw"),
        role=role,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


def _override_user(monkeypatch_user: User) -> None:
    async def _dep():
        return monkeypatch_user

    fastapi_app.dependency_overrides[get_current_user_optional] = _dep


@pytest.mark.asyncio
async def test_non_owner_designer_cannot_read_versions(client, db_session):
    owner = _make_user(UserRole.DESIGNER)
    intruder = _make_user(UserRole.DESIGNER)
    for u in (owner, intruder):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="Owned", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()

    _override_user(intruder)
    resp = await client.get(f"/designer/reports/{report.id}/versions")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_owner_designer_cannot_read_version_detail(client, db_session):
    owner = _make_user(UserRole.DESIGNER)
    intruder = _make_user(UserRole.DESIGNER)
    for u in (owner, intruder):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="Owned", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()

    _override_user(intruder)
    resp = await client.get(f"/designer/reports/{report.id}/versions/1")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_owner_designer_cannot_mutate_versions(client, db_session):
    owner = _make_user(UserRole.DESIGNER)
    intruder = _make_user(UserRole.DESIGNER)
    for u in (owner, intruder):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="Owned", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()

    _override_user(intruder)
    restore = await client.post(f"/designer/reports/{report.id}/versions/1/restore")
    tag = await client.post(
        f"/designer/reports/{report.id}/versions/1/tags", params={"tag_name": "x"}
    )
    assert restore.status_code == 403
    assert tag.status_code == 403


@pytest.mark.asyncio
async def test_owner_designer_can_read_own_versions(client, db_session):
    owner = _make_user(UserRole.DESIGNER)
    db_session.add(owner)
    await db_session.commit()

    report = Report(name="Owned", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()

    _override_user(owner)
    resp = await client.get(f"/designer/reports/{report.id}/versions")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_read_any_report_versions(client, db_session):
    owner = _make_user(UserRole.DESIGNER)
    admin = _make_user(UserRole.ADMIN)
    for u in (owner, admin):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="Owned", created_by=owner.id)
    db_session.add(report)
    await db_session.commit()

    _override_user(admin)
    resp = await client.get(f"/designer/reports/{report.id}/versions")
    assert resp.status_code == 200
