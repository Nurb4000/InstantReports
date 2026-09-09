"""Tests for report preview summary generator."""
from __future__ import annotations

from app.services.reports.preview_summary import generate_report_preview_summary


def test_returns_empty_for_none_definition():
    assert generate_report_preview_summary(None) == ""


def test_returns_empty_for_no_sections():
    definition = {"layout": {}}
    result = generate_report_preview_summary(definition)
    assert "No sections defined" in result


def test_shows_section_types_and_element_counts():
    definition = {
        "layout": {
            "sections": [
                {
                    "type": "header",
                    "elements": [
                        {"type": "text", "properties": {}},
                        {"type": "image", "properties": {}},
                    ],
                },
                {
                    "type": "detail",
                    "elements": [{"type": "table", "properties": {}}],
                },
            ]
        }
    }
    result = generate_report_preview_summary(definition)
    assert "Header:" in result
    assert "2 element(s)" in result
    assert "text" in result
    assert "image" in result
    assert "Detail:" in result
    assert "1 element(s)" in result
    assert "table" in result


def test_handles_missing_layout_key():
    definition = {}
    result = generate_report_preview_summary(definition)
    # Missing layout key means no sections, so shows "No sections defined"
    assert "No sections defined" in result
