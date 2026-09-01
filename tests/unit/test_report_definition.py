"""Unit tests for report definition normalization."""
from __future__ import annotations

import json

from app.services.report.definition import (
    DEFAULT_REPORT_DEFINITION,
    normalize_report_definition,
)


def _deepcopy(value):
    return json.loads(json.dumps(value))


def test_none_returns_default_shape():
    result = normalize_report_definition(None)
    assert result == DEFAULT_REPORT_DEFINITION
    assert result["layout"]["sections"] == []
    assert result["data_sources"] == []
    assert result["parameters"] == []


def test_empty_dict_returns_default_shape():
    assert normalize_report_definition({}) == DEFAULT_REPORT_DEFINITION


def test_partial_definition_merges_over_defaults():
    partial = {"layout": {"sections": [{"type": "header"}]}}
    result = normalize_report_definition(partial)
    # Merged key is preserved...
    assert result["layout"]["sections"] == [{"type": "header"}]
    # ...and missing keys are filled from defaults.
    assert result["data_sources"] == []
    assert result["parameters"] == []


def test_full_definition_is_preserved():
    full = {
        "layout": {"sections": [{"type": "detail"}]},
        "data_sources": [{"id": "ds1"}],
        "parameters": [{"name": "p1"}],
    }
    result = normalize_report_definition(full)
    assert result == full


def test_default_not_mutated_by_call():
    before = _deepcopy(DEFAULT_REPORT_DEFINITION)
    normalize_report_definition({"data_sources": [{"id": "x"}]})
    after = _deepcopy(DEFAULT_REPORT_DEFINITION)
    assert before == after
