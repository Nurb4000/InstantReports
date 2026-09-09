"""Tests for designer.py report helpers."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.routes.designer import _build_commit_message, _parse_definition_field


def _make_user(name="Test User"):
    return SimpleNamespace(name=name)


class TestParseDefinitionField:
    def test_returns_default_for_none(self):
        default = {"layout": {}}
        assert _parse_definition_field(None, default) == default

    def test_parses_valid_json_string(self):
        defn = {"layout": {"sections": [{"type": "detail"}]}}
        result = _parse_definition_field(json.dumps(defn), {})
        assert result == defn

    def test_returns_input_if_already_dict(self):
        defn = {"layout": {}}
        result = _parse_definition_field(defn, {})
        assert result == defn

    def test_returns_default_on_invalid_json(self):
        default = {"fallback": True}
        result = _parse_definition_field("not valid json{{{", default)
        assert result == default

    def test_returns_default_on_empty_string(self):
        default = {"layout": {}}
        result = _parse_definition_field("", default)
        assert result == default


class TestBuildCommitMessage:
    def test_uses_custom_message_with_username(self):
        user = _make_user("Alice")
        msg = _build_commit_message(user, "Fixed layout", "Updated")
        assert msg == "Alice (Fixed layout)"

    def test_uses_custom_message_without_username_when_user_is_none(self):
        msg = _build_commit_message(None, "Fixed layout", "Updated")
        # When user is None, falls back to "Unknown"
        assert msg == "Unknown (Fixed layout)"

    def test_uses_default_created_message(self):
        user = _make_user("Bob")
        msg = _build_commit_message(user, None, "Created")
        assert msg == "Created by Bob"

    def test_uses_default_updated_message(self):
        user = _make_user()
        msg = _build_commit_message(user, "", "Updated")
        assert msg == "Updated by Test User"

    def test_handles_whitespace_only_custom_message(self):
        user = _make_user()
        msg = _build_commit_message(user, "   ", "Created")
        assert msg == "Created by Test User"
