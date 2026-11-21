"""Integration tests for report soft-delete (Gap #9).

Covers the audit-safe delete model: deleting a report no longer physically
removes the row. Instead ``deleted_at`` is set so the report (and its
versions/outputs) stays in the database but is hidden from normal list and edit
views. Admins can inspect deleted rows with ``show_deleted`` and restore them,
which clears the marker. Non-admins cannot delete or restore.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

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


async def _seed_report(db, owner, name: str = "Audit Report") -> Report:
    report = Report(
        name=name,
        definition={"layout": {"sections": []}, "data_sources": [], "parameters": []},
        created_by=owner.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def _get_report_row(db, report_id) -> Report | None:
    result = await db.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_delete_is_soft_row_retained(client, db_session):
    admin = _make_user(UserRole.ADMIN, "a_soft_del@x.test")
    db_session.add(admin)
    await db_session.commit()
    report = await _seed_report(db_session, admin)

    _override_user(admin)
    resp = await client.delete(f"/designer/reports/{report.id}")
    assert resp.status_code == 200, resp.text

    # Row is physically retained; only the marker is set (audit trail intact).
    row = await _get_report_row(db_session, report.id)
    assert row is not None
    assert row.deleted_at is not None


@pytest.mark.asyncio
async def test_soft_deleted_hidden_from_list_default(client, db_session):
    admin = _make_user(UserRole.ADMIN, "a_list_hide@x.test")
    db_session.add(admin)
    await db_session.commit()
    report = await _seed_report(db_session, admin, name="Hidden After Delete")

    _override_user(admin)
    assert "Hidden After Delete" in (await client.get("/designer/reports")).text
    assert (await client.delete(f"/designer/reports/{report.id}")).status_code == 200

    listing = await client.get("/designer/reports")
    assert listing.status_code == 200
    assert "Hidden After Delete" not in listing.text


@pytest.mark.asyncio
async def test_admin_can_view_deleted_with_show_deleted(client, db_session):
    admin = _make_user(UserRole.ADMIN, "a_view_del@x.test")
    db_session.add(admin)
    await db_session.commit()
    report = await _seed_report(db_session, admin, name="Deleted But Retained")

    _override_user(admin)
    assert (await client.delete(f"/designer/reports/{report.id}")).status_code == 200

    listing = await client.get("/designer/reports?show_deleted=true")
    assert listing.status_code == 200
    assert "Deleted But Retained" in listing.text


@pytest.mark.asyncio
async def test_soft_deleted_hidden_from_edit_page(client, db_session):
    admin = _make_user(UserRole.ADMIN, "a_edit_hide@x.test")
    db_session.add(admin)
    await db_session.commit()
    report = await _seed_report(db_session, admin)

    _override_user(admin)
    assert (await client.delete(f"/designer/reports/{report.id}")).status_code == 200

    # The edit/read surface hides soft-deleted reports (404), but the row exists.
    assert (await client.get(f"/designer/reports/{report.id}")).status_code == 404
    assert await _get_report_row(db_session, report.id) is not None


@pytest.mark.asyncio
async def test_restore_makes_report_visible_again(client, db_session):
    admin = _make_user(UserRole.ADMIN, "a_restore@x.test")
    db_session.add(admin)
    await db_session.commit()
    report = await _seed_report(db_session, admin, name="Restore Me")

    _override_user(admin)
    assert (await client.delete(f"/designer/reports/{report.id}")).status_code == 200
    assert (await _get_report_row(db_session, report.id)).deleted_at is not None

    resp = await client.post(f"/designer/reports/{report.id}/restore")
    assert resp.status_code == 200, resp.text
    row = await _get_report_row(db_session, report.id)
    assert row.deleted_at is None

    # Visible in both the list and the edit/read surface again.
    assert "Restore Me" in (await client.get("/designer/reports")).text
    assert (await client.get(f"/designer/reports/{report.id}")).status_code == 200


@pytest.mark.asyncio
async def test_non_admin_cannot_delete(client, db_session):
    designer = _make_user(UserRole.DESIGNER, "d_cannot_del@x.test")
    admin = _make_user(UserRole.ADMIN, "a_del_owner@x.test")
    for u in (admin, designer):
        db_session.add(u)
    await db_session.commit()
    report = await _seed_report(db_session, admin)

    _override_user(designer)
    resp = await client.delete(f"/designer/reports/{report.id}")
    assert resp.status_code == 403
    # Row retained, marker not set.
    row = await _get_report_row(db_session, report.id)
    assert row is not None and row.deleted_at is None


@pytest.mark.asyncio
async def test_non_admin_cannot_restore(client, db_session):
    designer = _make_user(UserRole.DESIGNER, "d_cannot_restore@x.test")
    admin = _make_user(UserRole.ADMIN, "a_restore_owner@x.test")
    for u in (admin, designer):
        db_session.add(u)
    await db_session.commit()
    report = await _seed_report(db_session, admin)

    _override_user(admin)
    assert (await client.delete(f"/designer/reports/{report.id}")).status_code == 200

    _override_user(designer)
    resp = await client.post(f"/designer/reports/{report.id}/restore")
    assert resp.status_code == 403
    assert (await _get_report_row(db_session, report.id)).deleted_at is not None
