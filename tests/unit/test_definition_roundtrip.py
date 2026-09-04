"""Round-trip scan: definition shape emitted by serializeCanvas <-> backend render.

Verifies that a report definition shaped the way ``editor.html``'s
``serializeCanvas()`` produces it (per-element ``properties.query`` +
``properties.columns``, top-level ``data_sources`` / ``calculated_fields``) flows
correctly through ``fetch_element_data`` -> ``ReportRenderer`` -> exporter. This
is the backend half of the designer save/load round-trip and catches shape
mismatches between what the editor emits and what the engine consumes.
"""

from __future__ import annotations

import types

import pandas as pd

from app.services.engine.renderer import ReportRenderer
from app.services.exporters import export_report
from app.services.report.rendering import fetch_element_data


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


class _FakeResult:
    def __init__(self, item):
        self._item = item

    def scalar_one_or_none(self):
        return self._item


class _FakeDB:
    def __init__(self, connection):
        self._connection = connection

    async def execute(self, stmt):
        return _FakeResult(self._connection)


class _FakeConnector:
    def __init__(self, frames):
        # frames: dict[query-substring -> DataFrame] so we can distinguish the
        # table and chart element queries in a single definition.
        self.frames = frames
        self.calls = []

    async def execute_query(self, config, query, parameters=None):
        self.calls.append(query)
        for key, df in self.frames.items():
            if key in query:
                return df
        return pd.DataFrame()


def _make_definition() -> dict:
    """A definition shaped exactly like serializeCanvas() output."""
    return {
        "name": "RoundTrip",
        "layout": {
        "sections": [
            {
                "type": "detail",
                "hide_name": True,
                "custom_name": "Cover",
                "elements": [
                        {
                            "type": "table",
                            "properties": {
                                "query": "SELECT id, qty FROM orders",
                                "columns": [
                                    {"field": "id", "header": "Order"},
                                    {"field": "qty", "header": "Quantity"},
                                    {"field": "revenue", "header": "Revenue"},
                                ],
                                "showHeader": True,
                            },
                        }
                    ],
                },
                {
                    "type": "detail",
                    "elements": [
                        {
                            "type": "chart",
                            "properties": {
                                "query": "SELECT name, total FROM customers",
                                "xField": "name",
                                "yField": "total",
                                "type": "bar",
                            },
                        }
                    ],
                },
            ]
        },
        "data_sources": [
            {"connection_id": "00000000-0000-0000-0000-0000000000aa", "name": "orders", "type": "postgresql"}
        ],
        "parameters": [],
        "calculated_fields": [{"name": "revenue", "expression": "{{qty}} * 2"}],
        "query": "SELECT id, qty FROM orders",
        "selected_fields": [{"table": "orders", "field": "id"}],
    }


def test_roundtrip_table_columns_and_calculated_fields(monkeypatch):
    definition = _make_definition()
    conn = types.SimpleNamespace(connector_type="postgresql", config={})
    connector = _FakeConnector(
        {
            "orders": pd.DataFrame({"id": [1, 2], "qty": [3, 4]}),
            "customers": pd.DataFrame({"name": ["a", "b"], "total": [10, 20]}),
        }
    )
    monkeypatch.setattr("app.services.report.rendering.get_connector", lambda _: connector)

    element_data = asyncio_run(
        fetch_element_data(definition, _FakeDB(conn), label="rt")
    )

    # Every data-bearing element got a data_source key matching the fetched frame.
    table_el = definition["layout"]["sections"][0]["elements"][0]
    chart_el = definition["layout"]["sections"][1]["elements"][0]
    assert table_el["data_source"] in element_data
    assert chart_el["data_source"] in element_data

    rendered = ReportRenderer().render(definition, element_data)
    sections = rendered["sections"]

    # Header table: columns come from the element's properties.columns, rows carry
    # the computed calculated field 'revenue' (qty * 2).
    table_rendered = sections[0]["elements"][0]
    assert table_rendered["type"] == "table"
    assert [c["field"] for c in table_rendered["columns"]] == ["id", "qty", "revenue"]
    assert table_rendered["data"][0]["id"] == 1
    assert table_rendered["data"][0]["qty"] == 3
    # revenue (qty * 2) computed by DataProcessor on the export path.
    assert table_rendered["data"][0]["revenue"] == 6
    assert table_rendered["total_rows"] == 2

    # Detail chart: x/y fields resolved from properties, data frame attached.
    chart_rendered = sections[1]["elements"][0]
    assert chart_rendered["type"] == "chart"
    assert chart_rendered["x_field"] == "name"
    assert chart_rendered["y_field"] == "total"


def test_roundtrip_exporter_includes_data(monkeypatch):
    definition = _make_definition()
    conn = types.SimpleNamespace(connector_type="postgresql", config={})
    connector = _FakeConnector(
        {
            "orders": pd.DataFrame({"id": [1, 2], "qty": [3, 4]}),
            "customers": pd.DataFrame({"name": ["a", "b"], "total": [10, 20]}),
        }
    )
    monkeypatch.setattr("app.services.report.rendering.get_connector", lambda _: connector)
    element_data = asyncio_run(
        fetch_element_data(definition, _FakeDB(conn), label="rt")
    )
    rendered = ReportRenderer().render(definition, element_data)

    csv_bytes = export_report(rendered, "csv")
    assert isinstance(csv_bytes, bytes)
    # Column headers from properties.columns (revenue is a computed calculated field).
    assert b"Order" in csv_bytes and b"Quantity" in csv_bytes and b"Revenue" in csv_bytes
    # Actual row data survived the round-trip, including the computed 'revenue'
    # (qty * 2 -> 6, 8) proving calculated fields are applied on the export path.
    assert b"1,3,6" in csv_bytes and b"2,4,8" in csv_bytes
