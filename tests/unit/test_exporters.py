"""Unit tests for report exporters."""
from __future__ import annotations

import pandas as pd

from app.services.engine.renderer import ReportRenderer
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

        # CSV headers should use the friendly 'header' label, matching Excel/HTML.
        assert "Name" in csv
        assert "Alice" in csv
        assert "Bob" in csv


class TestRenderExportIntegration:
    """Verify the full pipeline: ReportRenderer output is consumable by exporters.

    The unit tests above feed hand-crafted dicts straight into the exporters, so
    they never catch a shape mismatch between what ``ReportRenderer`` produces and
    what the exporters expect. These tests render a real definition and pipe the
    result through each exporter.
    """

    def _rendered_sales_report(self) -> dict:
        df = pd.DataFrame({
            "product": ["Widget", "Gadget"],
            "price": [10, 25],
        })
        definition = {
            "name": "Sales",
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "data_source": "ds1",
                        "elements": [
                            {
                                "type": "table",
                                "data_source": "ds1",
                                "properties": {
                                    "columns": [
                                        {"field": "product", "header": "Product"},
                                        {"field": "price", "header": "Price"},
                                    ],
                                },
                            }
                        ],
                    }
                ]
            },
            "data_sources": {"ds1": df},
        }
        return ReportRenderer().render(definition, {"ds1": df})

    def test_rendered_report_exports_to_csv(self):
        rendered = self._rendered_sales_report()
        csv = CSVExporter().export(rendered).decode("utf-8")

        assert "Product" in csv and "Price" in csv
        assert "Widget" in csv and "Gadget" in csv
        assert "10" in csv and "25" in csv

    def test_rendered_report_exports_to_html(self):
        rendered = self._rendered_sales_report()
        html = HTMLExporter().export(rendered)

        assert "<table>" in html
        assert "Widget" in html and "Gadget" in html
        assert ">10<" in html or "10" in html

    def _rendered_report_with_chart(self) -> dict:
        df = pd.DataFrame({"region": ["N", "S", "E"], "sales": [100, 150, 75]})
        definition = {
            "name": "Sales by Region",
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "data_source": "ds1",
                        "elements": [
                            {
                                "type": "chart",
                                "data_source": "ds1",
                                "properties": {"type": "bar", "xField": "region", "yField": "sales"},
                            }
                        ],
                    }
                ]
            },
            "data_sources": {"ds1": df},
        }
        return ReportRenderer().render(definition, {"ds1": df})

    def test_html_export_inlines_chart_as_base64_png(self):
        """Charts must render in HTML (the browser-view format), not be dropped.

        PDF already renders charts; Excel/CSV are tabular and intentionally omit
        them. This closes the gap where an HTML export of a chart-only report
        produced an empty body.
        """
        rendered = self._rendered_report_with_chart()
        html = HTMLExporter().export(rendered)

        assert "data:image;base64," in html
        # The chart element carries the plotted DataFrame from the renderer.
        chart_element = rendered["sections"][0]["elements"][0]
        assert len(chart_element["data"]) == 3
