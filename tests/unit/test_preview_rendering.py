"""Native tests for the preview HTML renderer (app.routes.preview.render_report_with_data).

Covers the preview-vs-export rendering-consistency fix: table headers in the live preview must
honor the report element's configured column headers (matching export), not the raw SQL result
names. Without the fix, preview rendered snake_case result columns while export showed the
configured labels, so designers saw different column names depending on which view they used.
"""

from __future__ import annotations

import asyncio
import re

import pandas as pd

from app.routes import preview as preview_mod


def asyncio_run(coro):
    return asyncio.run(coro)


class _FakeConnector:
    def __init__(self, df):
        self._df = df

    async def execute_query(self, config, query, parameters=None):
        return self._df


class _FakeDB:
    """Minimal async session: preview commits/rollbacks around each element query."""

    async def commit(self):
        return None

    async def rollback(self):
        return None


async def _render(monkeypatch, definition, df):
    """Render a definition through the preview path with a fake connector."""
    from app.services import connectors

    async def _resolve(db, defn):
        # Non-empty config: the preview path gates table rendering on a truthy config.
        return _FakeConnector(df), {"host": "localhost", "port": 5432}

    # resolve_data_source_connector is imported locally inside render_report_with_data,
    # so patch it on its source module (the local `from ... import` reads it at call time).
    monkeypatch.setattr(connectors.base, "resolve_data_source_connector", _resolve)
    return await preview_mod.render_report_with_data(
        definition, definition["name"], "", db=_FakeDB()
    )


def _table_headers(html: str) -> list[str]:
    ths = re.findall(r"<th[^>]*>(.*?)</th>", html, flags=re.DOTALL)
    return [re.sub("<.*?>", "", t).strip() for t in ths]


def test_preview_honors_configured_column_headers(monkeypatch):
    # df columns are raw snake_case result names; the report configures display labels.
    df = pd.DataFrame({"country": ["USA"], "order_count": [5], "revenue": [100.0]})
    definition = {
        "name": "Hdr",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "table",
                "columns": [
                    {"field": "country", "header": "Country"},
                    {"field": "order_count", "header": "Orders"},
                    {"field": "revenue", "header": "Revenue"},
                ],
                "properties": {"query": "SELECT ..."},
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, df))
    assert _table_headers(html) == ["Country", "Orders", "Revenue"]


def test_preview_falls_back_to_raw_columns_when_unconfigured(monkeypatch):
    # Query-only tables (no configured columns) keep the original behavior: raw df names.
    df = pd.DataFrame({"country": ["USA"], "revenue": [100.0]})
    definition = {
        "name": "Raw",
        "layout": {"sections": [{"type": "detail", "elements": [
            {"type": "table", "properties": {"query": "SELECT ..."}}
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, df))
    assert _table_headers(html) == ["country", "revenue"]


def test_preview_cell_values_still_map_by_field(monkeypatch):
    # Data values must resolve by configured field even when the label differs.
    df = pd.DataFrame({"country": ["USA"], "revenue": [100.0]})
    definition = {
        "name": "Vals",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "table",
                "columns": [
                    {"field": "country", "header": "Country"},
                    {"field": "revenue", "header": "Revenue"},
                ],
                "properties": {"query": "SELECT ..."},
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, df))
    assert "USA" in html and "100.0" in html


def test_preview_escapes_untrusted_cell_values(monkeypatch):
    # Data-derived cell values (and chart labels) must be HTML-escaped so a
    # report pulling untrusted rows can't inject markup into the designer's
    # preview (stored XSS). Without escaping, <script> would reach the browser.
    df = pd.DataFrame({"country": ["<script>alert(1)</script>"], "revenue": [100.0]})
    definition = {
        "name": "XSS",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "table",
                "columns": [
                    {"field": "country", "header": "Country"},
                    {"field": "revenue", "header": "Revenue"},
                ],
                "properties": {"query": "SELECT ..."},
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, df))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_preview_renders_pie_chart_when_query_present(monkeypatch):
    # Regression: a chart with a configured query must execute it via the connector
    # and render an SVG pie. If the query is lost (e.g. wiped by the designer),
    # chart_data stays empty and the fallback placeholder takes its place.
    df = pd.DataFrame(
        {
            "category_name": ["Beverages", "Dairy Products"],
            "total_sales": [7612.50, 12853.25],
        }
    )
    definition = {
        "name": "Pie",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "chart",
                "properties": {
                    "title": "Sales Distribution by Category",
                    "type": "pie",
                    "xField": "category_name",
                    "yField": "total_sales",
                    "query": "SELECT c.category_name, SUM(od.unit_price * od.quantity) AS total_sales FROM categories c GROUP BY c.category_name",
                },
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, df))
    assert 'class="report-element chart-element"' in html
    assert "<svg" in html
    assert "Beverages" in html and "Dairy Products" in html
    assert "Chart requires data source connection to execute query" not in html


def test_preview_chart_falls_back_to_placeholder_without_query(monkeypatch):
    # A chart with an empty/missing query can't populate chart_data, so the preview
    # must degrade to the placeholder instead of crashing or rendering a broken SVG.
    definition = {
        "name": "PieNoQuery",
        "layout": {"sections": [{"type": "detail", "elements": [
            {"type": "chart", "properties": {"title": "Empty Chart", "type": "pie"}}
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, pd.DataFrame()))
    assert "Chart requires data source connection to execute query" in html


def test_preview_crosstab_renders_pivoted_table(monkeypatch):
    """Crosstab elements should pivot the query data into an HTML table."""
    df = pd.DataFrame({
        "region": ["North", "North", "South", "South"],
        "product": ["A", "B", "A", "B"],
        "sales": [100, 200, 300, 400],
    })
    definition = {
        "name": "Crosstab",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "crosstab",
                "properties": {
                    "rowField": "region",
                    "columnField": "product",
                    "valueField": "sales",
                    "aggregation": "sum",
                    "query": "SELECT * FROM sales",
                },
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, df))
    assert 'class="report-element crosstab-element"' in html
    # The pivoted table should contain row labels and column headers.
    assert "North" in html and "South" in html
    assert "A" in html and "B" in html
    # Values should be present (pivoted sums).
    assert "100" in html or "300" in html


def test_preview_crosstab_falls_back_without_query(monkeypatch):
    """Crosstab with no query should show a placeholder, not crash."""
    definition = {
        "name": "CrosstabNoQuery",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "crosstab",
                "properties": {
                    "rowField": "region",
                    "columnField": "product",
                    "valueField": "sales",
                    "aggregation": "sum",
                },
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, pd.DataFrame()))
    assert 'class="report-element crosstab-element"' in html
    assert "No data available for crosstab" in html


def test_preview_subreport_renders_inline(monkeypatch):
    """Subreport elements should render the referenced report inline."""
    import uuid

    sub_report_id = str(uuid.uuid4())
    sub_df = pd.DataFrame({"item": ["X", "Y"], "qty": [10, 20]})

    # The subreport's definition contains a table element.
    sub_definition = {
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "table",
                "properties": {"query": "SELECT * FROM items"},
            }
        ]}]},
    }

    # Main report references the subreport.
    main_definition = {
        "name": "Main",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "subreport",
                "properties": {
                    "reportId": sub_report_id,
                    "render_mode": "inline",
                },
            }
        ]}]},
    }

    # Patch the Report model lookup to return our fake subreport.
    from unittest.mock import MagicMock

    class _FakeReport:
        id = uuid.UUID(sub_report_id)
        name = "Sub Report"
        description = ""
        definition = sub_definition
        is_active = True

    async def _fake_execute(stmt):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _FakeReport()
        return mock_result

    fake_db = _FakeDB()
    fake_db.execute = _fake_execute

    # Patch resolve_data_source_connector to return a connector for the subreport too.
    from app.services import connectors

    async def _resolve(db, defn):
        return _FakeConnector(sub_df), {"host": "localhost", "port": 5432}

    monkeypatch.setattr(connectors.base, "resolve_data_source_connector", _resolve)

    html = asyncio_run(preview_mod.render_report_with_data(
        main_definition, "Main", "", db=fake_db
    ))
    assert 'class="report-element subreport-element"' in html
    # The subreport's table should be rendered inline.
    assert "X" in html and "Y" in html


def test_preview_subreport_falls_back_when_not_found(monkeypatch):
    """Subreport with an invalid ID should show a placeholder, not crash."""
    definition = {
        "name": "Main",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "subreport",
                "properties": {
                    "reportId": "00000000-0000-0000-0000-000000000001",
                    "render_mode": "inline",
                },
            }
        ]}]},
    }
    html = asyncio_run(_render(monkeypatch, definition, pd.DataFrame()))
    assert 'class="report-element subreport-element"' in html
    # Should show a fallback message since the report isn't found.
    assert "Sub-report not found" in html or "Sub-report" in html
