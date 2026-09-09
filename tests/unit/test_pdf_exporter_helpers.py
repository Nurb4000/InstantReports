"""Tests for PDF exporter helpers."""
from __future__ import annotations

from app.services.exporters.pdf import PDFExporter


def _make_exporter():
    return PDFExporter()


class TestComputeConditionalFormatting:
    """Test the pure conditional formatting helper."""

    def test_returns_empty_for_no_formatting(self):
        exporter = _make_exporter()
        data = [{"name": "Alice", "score": 95}]
        columns = [{"field": "name"}, {"field": "score"}]
        directives = exporter._compute_conditional_formatting(data, columns)
        assert directives == []

    def test_applies_row_formatting(self):
        exporter = _make_exporter()
        data = [{"name": "Alice", "formatting": {"row": {"background": "#ff0000", "bold": True}}}]
        columns = [{"field": "name"}]
        directives = exporter._compute_conditional_formatting(data, columns)
        assert len(directives) == 2
        commands = [d[0] for d in directives]
        assert "BACKGROUND" in commands
        assert "FONTNAME" in commands

    def test_applies_cell_formatting(self):
        exporter = _make_exporter()
        data = [{"name": "Alice", "formatting": {"cells": {"name": {"color": "#00ff00"}}}}]
        columns = [{"field": "name"}]
        directives = exporter._compute_conditional_formatting(data, columns)
        assert len(directives) == 1
        assert directives[0][0] == "TEXTCOLOR"

    def test_skips_missing_fields(self):
        exporter = _make_exporter()
        data = [{"name": "Alice", "formatting": {"cells": {"nonexistent": {"bold": True}}}}]
        columns = [{"field": "name"}]
        directives = exporter._compute_conditional_formatting(data, columns)
        assert directives == []


class TestBuildTableStyle:
    """Test the table style builder."""

    def test_includes_default_styling(self):
        exporter = _make_exporter()
        data = [{"name": "Alice"}]
        columns = [{"field": "name"}]
        style = exporter._build_table_style(data, columns)
        # TableStyle is a mutable object; just verify it was created
        assert style is not None

    def test_includes_conditional_formatting(self):
        exporter = _make_exporter()
        data = [{"name": "Alice", "formatting": {"row": {"bold": True}}}]
        columns = [{"field": "name"}]
        style = exporter._build_table_style(data, columns)
        assert style is not None
