"""Tests for admin.py schedule form parsing helpers."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.routes.admin import (
    _parse_json_body,
    _parse_optional_uuid,
    _validate_output_format,
)


class TestParseJsonBody:
    def test_returns_form_fields_when_body_is_none(self):
        form_fields = {"name": "default", "cron": "* * * * *"}
        result = _parse_json_body(None, form_fields)
        assert result == form_fields

    def test_merges_json_body_over_form_defaults(self):
        form_fields = {"name": "default", "cron": "* * * * *", "timezone": "UTC"}
        body = {"name": "json_name", "timezone": "US/Eastern"}
        result = _parse_json_body(body, form_fields)
        assert result["name"] == "json_name"
        assert result["cron"] == "* * * * *"
        assert result["timezone"] == "US/Eastern"

    def test_keeps_form_fields_when_json_has_null(self):
        form_fields = {"name": "default", "value": "keep"}
        body = {"name": None, "value": None}
        result = _parse_json_body(body, form_fields)
        # None values in JSON should not override form defaults
        assert result["name"] == "default"
        assert result["value"] == "keep"


class TestValidateOutputFormat:
    def test_returns_default_for_none(self):
        assert _validate_output_format(None) == "pdf"

    def test_returns_default_for_empty_string(self):
        assert _validate_output_format("") == "pdf"

    def test_normalizes_valid_format(self):
        assert _validate_output_format("xlsx") == "xlsx"
        assert _validate_output_format("CSV") == "csv"
        assert _validate_output_format("Html") == "html"

    def test_raises_400_for_invalid_format(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_output_format("invalid")
        assert exc_info.value.status_code == 400
        assert "Invalid output_format" in exc_info.value.detail


class TestParseOptionalUuid:
    def test_returns_none_for_empty_string(self):
        assert _parse_optional_uuid("") is None

    def test_returns_none_for_none(self):
        assert _parse_optional_uuid(None) is None

    def test_parses_valid_uuid(self):
        uid = uuid.uuid4()
        assert _parse_optional_uuid(str(uid)) == uid

    def test_returns_none_for_invalid_uuid(self):
        assert _parse_optional_uuid("not-a-uuid") is None
        assert _parse_optional_uuid("12345") is None
