"""Tests for version comparison service."""
from __future__ import annotations

from app.services.versioning.compare import compare_versions


def test_compare_no_changes():
    """Identical definitions should show no changes."""
    definition = {
        "layout": {"sections": [{"type": "detail", "elements": []}]},
        "data_sources": [],
        "parameters": [],
    }
    comparison = compare_versions(definition, definition)
    assert comparison["summary"]["has_changes"] is False
    assert comparison["summary"]["sections_modified"] == 0


def test_compare_added_section():
    """A section in new but not old should be marked as added."""
    old_def = {"layout": {"sections": []}, "data_sources": [], "parameters": []}
    new_def = {
        "layout": {"sections": [{"type": "header", "elements": []}]},
        "data_sources": [],
        "parameters": [],
    }
    comparison = compare_versions(old_def, new_def)
    assert comparison["summary"]["sections_added"] == 1
    assert len(comparison["sections"]) == 1
    assert comparison["sections"][0]["status"] == "added"


def test_compare_removed_section():
    """A section in old but not new should be marked as removed."""
    old_def = {
        "layout": {"sections": [{"type": "header", "elements": []}]},
        "data_sources": [],
        "parameters": [],
    }
    new_def = {"layout": {"sections": []}, "data_sources": [], "parameters": []}
    comparison = compare_versions(old_def, new_def)
    assert comparison["summary"]["sections_removed"] == 1
    assert len(comparison["sections"]) == 1
    assert comparison["sections"][0]["status"] == "removed"


def test_compare_modified_section():
    """A modified section should show changes."""
    old_def = {
        "layout": {"sections": [{"type": "detail", "elements": [{"type": "text", "content": "old"}]}]},
        "data_sources": [],
        "parameters": [],
    }
    new_def = {
        "layout": {"sections": [{"type": "detail", "elements": [{"type": "text", "content": "new"}]}]},
        "data_sources": [],
        "parameters": [],
    }
    comparison = compare_versions(old_def, new_def)
    assert comparison["summary"]["sections_modified"] == 1
    assert len(comparison["sections"]) == 1
    assert comparison["sections"][0]["status"] == "modified"
    assert len(comparison["sections"][0]["changes"]) > 0


def test_compare_added_data_source():
    """A data source in new but not old should be marked as added."""
    old_def = {"layout": {"sections": []}, "data_sources": [], "parameters": []}
    new_def = {
        "layout": {"sections": []},
        "data_sources": [{"id": "ds1", "name": "Test"}],
        "parameters": [],
    }
    comparison = compare_versions(old_def, new_def)
    assert comparison["summary"]["data_sources_added"] == 1


def test_compare_modified_parameter():
    """A modified parameter should be detected."""
    old_def = {
        "layout": {"sections": []},
        "data_sources": [],
        "parameters": [{"name": "year", "type": "integer", "default": 2023}],
    }
    new_def = {
        "layout": {"sections": []},
        "data_sources": [],
        "parameters": [{"name": "year", "type": "integer", "default": 2024}],
    }
    comparison = compare_versions(old_def, new_def)
    assert len(comparison["parameters"]) == 1
    assert comparison["parameters"][0]["status"] == "modified"
