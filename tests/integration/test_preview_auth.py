"""Integration tests for preview-route authorization.

Covers round-8: `GET /preview/temp` is an arbitrary-query preview tool — a
client-supplied definition's element queries execute against an application
data-source connection. It must be gated to admin/designer like
`preview_report`, otherwise any authenticated user (including low-privilege
viewers) could run client-supplied SQL against configured databases.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.auth import hash_password
from app.models.user import AuthSource, User, UserRole


async def _login(client, email: str, password: str):
    resp = await client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.headers["Cookie"] = f"access_token={resp.cookies.get('access_token')}"
    return client


@pytest.mark.asyncio
async def test_viewer_cannot_use_temp_preview(client, db_session):
    viewer = User(
        id=uuid.uuid4(),
        email="viewer@preview.test",
        name="Viewer",
        password_hash=hash_password("pw"),
        role=UserRole.VIEWER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(viewer)
    await db_session.commit()

    client = await _login(client, "viewer@preview.test", "pw")

    # A definition carrying a table query that would execute against a data source.
    definition = {
        "layout": {
            "sections": [
                {
                    "type": "detail",
                    "elements": [
                        {
                            "type": "table",
                            "properties": {"query": "SELECT * FROM users"},
                        },
                    ],
                },
            ]
        },
    }
    resp = await client.get(
        "/preview/temp", params={"definition_json": json.dumps(definition)}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_designer_can_use_temp_preview(client, db_session):
    designer = User(
        id=uuid.uuid4(),
        email="dev@preview.test",
        name="Dev",
        password_hash=hash_password("pw"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(designer)
    await db_session.commit()

    client = await _login(client, "dev@preview.test", "pw")

    resp = await client.get(
        "/preview/temp",
        params={"definition_json": json.dumps({"layout": {"sections": []}})},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_export_temp_definition(client, db_session):
    viewer = User(
        id=uuid.uuid4(),
        email="viewer-export@preview.test",
        name="Viewer",
        password_hash=hash_password("pw"),
        role=UserRole.VIEWER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(viewer)
    await db_session.commit()

    client = await _login(client, "viewer-export@preview.test", "pw")

    resp = await client.get(
        "/preview/export",
        params={
            "definition_json": json.dumps({"layout": {"sections": []}}),
            "format": "html",
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_designer_can_export_temp_definition(client, db_session):
    designer = User(
        id=uuid.uuid4(),
        email="dev-export@preview.test",
        name="Dev",
        password_hash=hash_password("pw"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(designer)
    await db_session.commit()

    client = await _login(client, "dev-export@preview.test", "pw")

    resp = await client.get(
        "/preview/export",
        params={
            "definition_json": json.dumps(
                {"name": "My Report", "layout": {"sections": []}}
            ),
            "format": "html",
        },
    )
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "My_Report.html" in resp.headers["content-disposition"]


@pytest.mark.asyncio
async def test_export_route_not_swallowed_by_report_id(client, db_session):
    # Regression: /preview/export must be matched before the /{report_id} path
    # param, otherwise "export" fails UUID validation and returns 422.
    designer = User(
        id=uuid.uuid4(),
        email="dev-order@preview.test",
        name="Dev",
        password_hash=hash_password("pw"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(designer)
    await db_session.commit()

    client = await _login(client, "dev-order@preview.test", "pw")

    resp = await client.get(
        "/preview/export",
        params={
            "definition_json": json.dumps({"layout": {"sections": []}}),
            "format": "html",
        },
    )
    assert resp.status_code == 200
