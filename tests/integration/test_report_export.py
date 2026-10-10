"""Integration test for the report definition export endpoint.

Covers the P0.5 / H3 fix: _build_report_export_data called db.execute() without
awaiting it (crashing on any export whose data source had a connection) and only
denylisted password/secret when embedding connection config, leaking other
credentials such as auth_token/api_key.
"""
from __future__ import annotations

import uuid

from app.models.connection import DataConnection
from app.models.report import Report


async def test_export_report_redacts_connection_secrets(auth_client, db_session):
    conn = DataConnection(
        id=uuid.uuid4(),
        name="prod-db",
        connector_type="postgresql",
        config={
            "host": "db.internal",
            "port": 5432,
            "database": "app",
            "user": "app",
            "password": "p@ss",
            "auth_token": "tok_live_123",
            "api_key": "key_123",
        },
        created_by=uuid.uuid4(),
    )
    db_session.add(conn)
    await db_session.commit()

    report = Report(
        name="Export Me",
        created_by=uuid.uuid4(),
        definition={
            "layout": {"sections": []},
            "data_sources": [{"connection_id": str(conn.id), "name": "ds"}],
        },
    )
    db_session.add(report)
    await db_session.commit()

    resp = await auth_client.get(f"/designer/reports/{report.id}/export")
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    ds = payload["report"]["definition"]["data_sources"][0]
    template = ds["connection_template"]
    assert template["host"] == "db.internal"
    assert template["database"] == "app"
    for leaked in ("password", "secret", "auth_token", "api_key", "dsn"):
        assert leaked not in template
    assert "connection_id" not in ds
