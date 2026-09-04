"""Native tests for app.services.report.rendering.

Covers the shared render/fetch core that both the scheduled-export path
(runner.execute_report) and the on-demand export route use, so the logic lives
in one place and is testable without the runner's delivery-stack imports.
"""

from __future__ import annotations

import asyncio
import types

import pandas as pd
import pytest

from app.services.exporters import normalize_output_format
from app.services.report.rendering import fetch_element_data, render_report_bytes


def asyncio_run(coro):
    return asyncio.run(coro)


def _rendered_report() -> dict:
    return {
        "name": "Test",
        "layout": {"sections": [{"type": "detail", "elements": [
            {
                "type": "table",
                "data_source": "ds_0",
                "columns": [{"field": "a", "header": "A"}, {"field": "b", "header": "B"}],
            }
        ]}]},
    }


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 3], "b": [2, 4]})


def test_render_report_bytes_csv():
    data = render_report_bytes(_rendered_report(), {"ds_0": _sample_df()}, "csv")
    assert isinstance(data, bytes)
    assert b"A,B" in data and b"1,2" in data


def test_render_report_bytes_html_normalizes_str_to_bytes():
    # HTMLExporter returns str; export_report() must normalize to bytes.
    data = render_report_bytes(_rendered_report(), {"ds_0": _sample_df()}, "html")
    assert isinstance(data, bytes)
    assert b"<table" in data


def test_render_report_bytes_reuses_format_normalization():
    # Route/runner share this path; the alias must resolve consistently.
    assert normalize_output_format("EXCEL") == "xlsx"
    assert normalize_output_format("pdf") == "pdf"


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


def test_fetch_element_data_dispatches_to_connector(monkeypatch):
    """The primary connection's connector runs each element query; results are
    keyed so the renderer can locate them via element['data_source']."""
    df = pd.DataFrame({"x": [1, 2, 3]})

    class _FakeConnector:
        def __init__(self):
            self.calls = []

        async def execute_query(self, config, query, parameters):
            self.calls.append((config, query, parameters))
            return df

    fake_conn = types.SimpleNamespace(connector_type="csv", config={"file_path": "/tmp/x.csv"})
    fake_connector = _FakeConnector()
    monkeypatch.setattr("app.services.report.rendering.get_connector", lambda _: fake_connector)

    definition = {
        "data_sources": [{"connection_id": "00000000-0000-0000-0000-000000000001"}],
        "layout": {"sections": [{"type": "detail", "elements": [
            {"type": "table", "properties": {"query": "SELECT x"}}]}]},
    }
    db = _FakeDB(fake_conn)

    element_data = asyncio_run(fetch_element_data(definition, db, parameters={"$x": 1}, label="test"))

    assert list(element_data) == ["ds_0"]
    assert list(element_data["ds_0"]["x"]) == [1, 2, 3]
    # parameter was threaded through to the connector query
    assert fake_connector.calls[0][2] == {"$x": 1}
    # element data_source was set so the renderer can resolve it
    assert definition["layout"]["sections"][0]["elements"][0]["data_source"] == "ds_0"


def test_fetch_element_data_empty_when_no_connections(monkeypatch):
    monkeypatch.setattr(
        "app.services.report.rendering.get_connector",
        lambda _: pytest.fail("connector must not be used without a connection"),
    )
    definition = {"data_sources": [], "layout": {"sections": []}}
    element_data = asyncio_run(fetch_element_data(definition, _FakeDB(None), label="empty"))
    assert element_data == {}
