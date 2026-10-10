"""Tests for designer route helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routes.designer import _build_report_export_data


def _make_report(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "name": "Test Report",
        "description": "A test report",
        "definition": {"layout": {"sections": []}},
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_user():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_build_report_export_data_includes_metadata():
    report = _make_report()
    user = _make_user()
    data = await _build_report_export_data(report, user, None)

    assert data["instantreports_export"] is True
    assert data["version"] == "1.0"
    assert data["report"]["name"] == "Test Report"
    assert data["report"]["description"] == "A test report"
    # Export always includes data_sources field for self-containment
    assert "data_sources" in data["report"]["definition"]
    assert data["exported_by"] == str(user.id)
    assert data["exported_at"] == "2024-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_build_report_export_data_handles_missing_description():
    report = _make_report(description=None)
    user = _make_user()
    data = await _build_report_export_data(report, user, None)
    assert data["report"]["description"] == ""


@pytest.mark.asyncio
async def test_build_report_export_data_handles_missing_definition():
    report = _make_report(definition=None)
    user = _make_user()
    data = await _build_report_export_data(report, user, None)
    # Export always includes data_sources field for self-containment
    assert "data_sources" in data["report"]["definition"]


@pytest.mark.asyncio
async def test_build_report_export_data_handles_naive_updated_at():
    report = _make_report(updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    user = _make_user()
    data = await _build_report_export_data(report, user, None)
    assert data["exported_at"] == "2024-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_build_report_export_data_redacts_connection_secrets_and_copies_definition():
    """Connection config embedded in the export must be allow-listed.

    Regression (H3): the old code only denylisted password/secret, so keys like
    auth_token/api_key leaked, and it mutated report.definition in place.
    """
    original_definition = {
        "layout": {"sections": []},
        "data_sources": [{"connection_id": str(uuid.uuid4()), "name": "ds"}],
    }
    report = _make_report(definition=original_definition)
    user = _make_user()

    conn = SimpleNamespace(
        name="prod-db",
        connector_type="postgresql",
        config={
            "host": "db.internal",
            "port": 5432,
            "database": "app",
            "user": "app",
            "password": "p@ss",
            "secret": "shh",
            "auth_token": "tok_live_123",
            "api_key": "key_123",
            "dsn": "postgres://u:p@h/db",
        },
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = conn
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    data = await _build_report_export_data(report, user, db)

    ds = data["report"]["definition"]["data_sources"][0]
    template = ds["connection_template"]
    assert template["name"] == "prod-db"
    assert template["connector_type"] == "postgresql"
    assert template["host"] == "db.internal"
    assert template["database"] == "app"
    # No credential-bearing keys may survive the allow-list.
    for leaked in ("password", "secret", "auth_token", "api_key", "dsn"):
        assert leaked not in template
    # connection_id is replaced by the embedded template
    assert "connection_id" not in ds

    # The live report definition must not be mutated by an export.
    assert "connection_id" in original_definition["data_sources"][0]
    assert "connection_template" not in original_definition["data_sources"][0]
