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
    ths = re.findall(r"<th[^>]*>(.*?)</th>", html, flags=re.S)
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
