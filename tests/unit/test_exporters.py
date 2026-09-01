"""Unit tests for report exporters."""
from __future__ import annotations

from app.services.exporters.excel_csv_html import CSVExporter, HTMLExporter


class TestHTMLExporter:
    """Test HTML export with conditional formatting."""

    def test_table_applies_row_and_cell_formatting(self):
        exporter = HTMLExporter()
        rendered = {
            "name": "Report",
            "sections": [
                {
                    "type": "detail",
                    "elements": [
                        {
                            "type": "table",
                            "columns": [
                                {"field": "name", "header": "Name"},
                                {"field": "score", "header": "Score"},
                            ],
                            "data": [
                                {
                                    "name": "Alice",
                                    "score": 95,
                                    "formatting": {
                                        "row": None,
                                        "cells": {"score": {"color": "#00ff00"}},
                                    },
                                },
                                {
                                    "name": "Bob",
                                    "score": 60,
                                    "formatting": {
                                        "row": {"background": "#ffcccc"},
                                        "cells": {"score": None},
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }

        html = exporter.export(rendered)

        assert "background-color: #ffcccc" in html  # Bob's row
        assert "color: #00ff00" in html  # Alice's score cell

    def test_table_without_formatting_still_renders(self):
        exporter = HTMLExporter()
        rendered = {
            "name": "Report",
            "sections": [
                {
                    "type": "detail",
                    "elements": [
                        {
                            "type": "table",
                            "columns": [{"field": "name", "header": "Name"}],
                            "data": [{"name": "Alice"}],
                        }
                    ],
                }
            ],
        }

        html = exporter.export(rendered)

        assert "<td>Alice</td>" in html
        assert "#ffcccc" not in html
        assert "#00ff00" not in html


class TestCSVExporter:
    """Test CSV export."""

    def test_csv_writes_detail_tables(self):
        exporter = CSVExporter()
        rendered = {
            "name": "Report",
            "sections": [
                {
                    "type": "detail",
                    "elements": [
                        {
                            "type": "table",
                            "columns": [{"field": "name", "header": "Name"}],
                            "data": [{"name": "Alice"}, {"name": "Bob"}],
                        }
                    ],
                }
            ],
        }

        csv = exporter.export(rendered).decode("utf-8")

        assert "name" in csv
        assert "Alice" in csv
        assert "Bob" in csv
