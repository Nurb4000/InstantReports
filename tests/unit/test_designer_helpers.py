"""Tests for designer route helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

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


def test_build_report_export_data_includes_metadata():
    report = _make_report()
    user = _make_user()
    data = _build_report_export_data(report, user)

    assert data["instantreports_export"] is True
    assert data["version"] == "1.0"
    assert data["report"]["name"] == "Test Report"
    assert data["report"]["description"] == "A test report"
    assert data["report"]["definition"] == {"layout": {"sections": []}}
    assert data["exported_by"] == str(user.id)
    assert data["exported_at"] == "2024-01-01T00:00:00+00:00"


def test_build_report_export_data_handles_missing_description():
    report = _make_report(description=None)
    user = _make_user()
    data = _build_report_export_data(report, user)
    assert data["report"]["description"] == ""


def test_build_report_export_data_handles_missing_definition():
    report = _make_report(definition=None)
    user = _make_user()
    data = _build_report_export_data(report, user)
    assert data["report"]["definition"] == {}


def test_build_report_export_data_handles_naive_updated_at():
    report = _make_report(updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    user = _make_user()
    data = _build_report_export_data(report, user)
    assert data["exported_at"] == "2024-01-01T00:00:00+00:00"
