"""Tests that schedule read endpoints do not leak delivery secrets.

Covers the round-7c fix: ``list_schedules`` and ``get_schedule_api`` previously
returned the full ``delivery_config`` verbatim, which for SFTP/SMB deliveries
contains the plaintext ``password`` and for webhook deliveries contains the
``secret``. Any admin/designer could enumerate every schedule and read those
secrets. The config is now redacted before it is serialized.
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


def _user(role: UserRole) -> User:
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
async def test_schedule_read_endpoints_redact_delivery_secrets(
    client, db_session, monkeypatch,
):
    admin = _user(UserRole.ADMIN)
    designer = _user(UserRole.DESIGNER)
    for u in (admin, designer):
        db_session.add(u)
    await db_session.commit()

    report = Report(name="R", created_by=admin.id)
    db_session.add(report)
    await db_session.commit()

    schedule = Schedule(
        report_id=report.id,
        name="sftp-job",
        owner_id=admin.id,
        created_by=admin.id,
        delivery_type="sftp",
        delivery_config={
            "type": "sftp",
            "host": "ftp.example.com",
            "port": 22,
            "username": "u",
            "password": "s3cret-pw",
            "remote_path": "/",
        },
    )
    webhook = Schedule(
        report_id=report.id,
        name="webhook-job",
        owner_id=admin.id,
        created_by=admin.id,
        delivery_type="webhook",
        delivery_config={
            "type": "webhook",
            "url": "https://example.com/hook",
            "secret": "whsec_toplevel",
        },
    )
    db_session.add_all([schedule, webhook])
    await db_session.commit()

    _override_user(designer)

    listed = await client.get("/admin/api/schedules")
    assert listed.status_code == 200, listed.text
    bodies = {s["name"]: s for s in listed.json()}
    assert "s3cret-pw" not in listed.text
    assert "whsec_toplevel" not in listed.text
    assert bodies["sftp-job"]["delivery_config"]["password"] == "***REDACTED***"
    assert bodies["sftp-job"]["delivery_config"]["host"] == "ftp.example.com"
    assert bodies["webhook-job"]["delivery_config"]["secret"] == "***REDACTED***"

    single = await client.get(f"/admin/api/schedules/{schedule.id}")
    assert single.status_code == 200, single.text
    assert "s3cret-pw" not in single.text
    assert single.json()["delivery_config"]["password"] == "***REDACTED***"
