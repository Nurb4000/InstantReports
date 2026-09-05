"""Integration tests for portal report-output visibility by role.

Verifies the admin/developer visibility parity with the report catalog fix in
designer.py::list_reports: admins see every executed output, while non-admins
only see outputs for reports whose schedules they own.
"""
from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.models.connection import Schedule
from app.models.report import Report, ReportOutput
from app.models.user import AuthSource, User, UserRole


async def _login(client, email: str, password: str):
    """Log in as a user and attach the auth cookie to the client."""
    resp = await client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    token = resp.cookies.get("access_token")
    client.headers["Cookie"] = f"access_token={token}"
    return client


@pytest.mark.asyncio
async def test_admin_sees_all_report_outputs(client, db_session):
    """Admins see outputs for reports they didn't schedule; non-admins do not."""
    admin = User(
        id=uuid.uuid4(), email="admin@portal.test", name="Admin",
        password_hash=hash_password("pw"), role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL, is_active=True,
    )
    designer = User(
        id=uuid.uuid4(), email="dev@portal.test", name="Dev",
        password_hash=hash_password("pw"), role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL, is_active=True,
    )
    db_session.add_all([admin, designer])
    await db_session.commit()

    # A report whose only schedule is owned by the admin.
    report = Report(
        id=uuid.uuid4(), name="Admin-Scheduled Report", created_by=admin.id,
    )
    db_session.add(report)
    await db_session.commit()

    schedule = Schedule(
        id=uuid.uuid4(), report_id=report.id, owner_id=admin.id, name="Admin Job",
        created_by=admin.id,
    )
    db_session.add(schedule)
    await db_session.commit()

    output = ReportOutput(
        id=uuid.uuid4(), report_id=report.id, schedule_id=schedule.id,
        format="csv", file_name="job.csv", file_data=b"x", file_size=1,
        mime_type="text/csv",
    )
    db_session.add(output)
    await db_session.commit()

    # A non-admin designer does not own this schedule -> must not see it.
    designer_client = await _login(client, "dev@portal.test", "pw")
    resp = await designer_client.get("/portal/")
    assert resp.status_code == 200
    assert "Admin-Scheduled Report" not in resp.text

    # An admin sees every output regardless of schedule ownership.
    admin_client = await _login(client, "admin@portal.test", "pw")
    resp = await admin_client.get("/portal/")
    assert resp.status_code == 200
    assert "Admin-Scheduled Report" in resp.text


@pytest.mark.asyncio
async def test_designer_sees_own_scheduled_outputs(client, db_session):
    """Non-admins still see outputs for reports they own schedules for."""
    designer = User(
        id=uuid.uuid4(), email="dev2@portal.test", name="Dev2",
        password_hash=hash_password("pw"), role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL, is_active=True,
    )
    db_session.add(designer)
    await db_session.commit()

    report = Report(
        id=uuid.uuid4(), name="Dev-Scheduled Report", created_by=designer.id,
    )
    db_session.add(report)
    await db_session.commit()

    schedule = Schedule(
        id=uuid.uuid4(), report_id=report.id, owner_id=designer.id, name="Dev Job",
        created_by=designer.id,
    )
    db_session.add(schedule)
    await db_session.commit()

    output = ReportOutput(
        id=uuid.uuid4(), report_id=report.id, schedule_id=schedule.id,
        format="pdf", file_name="job.pdf", file_data=b"x", file_size=1,
        mime_type="application/pdf",
    )
    db_session.add(output)
    await db_session.commit()

    designer_client = await _login(client, "dev2@portal.test", "pw")
    resp = await designer_client.get("/portal/")
    assert resp.status_code == 200
    assert "Dev-Scheduled Report" in resp.text
