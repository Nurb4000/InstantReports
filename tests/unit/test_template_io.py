"""Unit tests for template export/import serialization."""
from __future__ import annotations

import uuid

import pytest

from app.models.connection import QueryTemplate
from app.services.query_builder import template_io


def _make_template(**overrides) -> QueryTemplate:
    kwargs = {
        "name": "Sample",
        "description": "desc",
        "connection_id": uuid.uuid4(),
        "query_config": {"select": [], "from_tables": ["orders"]},
    }
    kwargs.update(overrides)
    return QueryTemplate(**kwargs)


def test_template_to_dict_roundtrip():
    template = _make_template(name="Orders")
    data = template_io.template_to_dict(template)
    assert data["name"] == "Orders"
    assert data["description"] == "desc"
    assert data["query_config"] == {"select": [], "from_tables": ["orders"]}
    assert uuid.UUID(data["connection_id"]) == template.connection_id


def test_export_templates_wraps_bundle():
    templates = [_make_template(), _make_template(name="Second")]
    bundle = template_io.export_templates(templates)
    assert bundle["version"] == template_io.EXPORT_VERSION
    assert len(bundle["templates"]) == 2
    assert [t["name"] for t in bundle["templates"]] == ["Sample", "Second"]


def test_parse_import_accepts_bundle():
    bundle = {
        "version": "1.0",
        "templates": [
            {"name": "A", "query_config": {"from_tables": ["t"]}},
            {"name": "B", "query_config": {"from_tables": ["u"]}},
        ],
    }
    items = template_io.parse_import_payload(bundle)
    assert [i["name"] for i in items] == ["A", "B"]
    assert all("connection_id" in i for i in items)


def test_parse_import_accepts_single_template():
    single = {"name": "Solo", "query_config": {"from_tables": ["t"]}}
    items = template_io.parse_import_payload(single)
    assert len(items) == 1
    assert items[0]["name"] == "Solo"


def test_parse_import_coerces_json_string_config():
    payload = {"name": "Str", "query_config": '{"from_tables": ["t"]}'}
    items = template_io.parse_import_payload(payload)
    assert items[0]["query_config"] == {"from_tables": ["t"]}


def test_parse_import_defaults_missing_name():
    items = template_io.parse_import_payload({"query_config": {"from_tables": ["t"]}})
    assert items[0]["name"] == "Imported Template"


def test_parse_import_rejects_missing_query_config():
    with pytest.raises(ValueError, match="query_config"):
        template_io.parse_import_payload({"name": "X"})


def test_parse_import_rejects_bad_connection_id():
    with pytest.raises(ValueError, match="connection_id"):
        template_io.parse_import_payload(
            {"name": "X", "connection_id": "not-a-uuid", "query_config": {}}
        )


def test_parse_import_rejects_non_object():
    with pytest.raises(ValueError):
        template_io.parse_import_payload(["not", "a", "dict"])


def test_parse_import_rejects_non_dict_templates():
    with pytest.raises(ValueError):
        template_io.parse_import_payload({"templates": 5})
