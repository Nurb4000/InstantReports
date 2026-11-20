"""Native tests for CSV/Excel connector execute_query.

These two connectors run on pandas (no external DB driver needed), unlike the
SQL connectors. They also cover the B7 fix: the Excel connector must apply
parameters even when ``pandasql`` is unavailable (it is not installed in the
minimal test env) instead of silently returning unfiltered rows.
"""

from __future__ import annotations

import csv

import pandas as pd
import pytest

import app.services.connectors.csv_excel as csv_excel_mod
from app.services.connectors.csv_excel import CSVConnector, ExcelConnector


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Isolate connector file resolution to a temp directory.

    With the M1 fix, absolute ``file_path`` values must resolve within
    ``settings.DATA_ROOT``; point that root at an isolated tmp dir so tests do
    not depend on (or escape) the repo's real ``data/`` folder.
    """
    monkeypatch.setattr(csv_excel_mod.settings, "DATA_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def csv_file(data_root):
    path = data_root / "sales.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["region", "revenue"])
        for r, v in [("N", 100), ("S", 150), ("E", 75), ("W", 200)]:
            writer.writerow([r, v])
    return str(path)


async def test_csv_applies_parameter_filter(csv_file):
    connector = CSVConnector()
    df = await connector.execute_query(
        {"file_path": csv_file}, "SELECT *", parameters={"$region": "N"}
    )
    assert list(df["region"]) == ["N"]
    assert int(df["revenue"].iloc[0]) == 100


async def test_csv_without_parameters_returns_all(csv_file):
    connector = CSVConnector()
    df = await connector.execute_query({"file_path": csv_file}, "SELECT *")
    assert len(df) == 4


async def test_excel_applies_parameter_filter_without_pandasql(monkeypatch):
    expected = pd.DataFrame({"region": ["N", "S"], "revenue": [100, 150]})

    def fake_read_excel(*args, **kwargs):
        return expected

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    connector = ExcelConnector()
    df = await connector.execute_query(
        {"file_path": "dummy.xlsx"}, "SELECT *", parameters={"$region": "N"}
    )
    # pandasql is not installed; params must still filter (B7 regression guard).
    assert set(df["region"]) == {"N"}


async def test_excel_query_without_params_returns_full_sheet(monkeypatch):
    expected = pd.DataFrame({"region": ["N", "S", "E", "W"], "revenue": [100, 150, 75, 200]})

    def fake_read_excel(*args, **kwargs):
        return expected

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    connector = ExcelConnector()
    df = await connector.execute_query({"file_path": "dummy.xlsx"}, "SELECT *")
    assert len(df) == 4


async def test_csv_schema_uses_filename_as_table_name(csv_file):
    # Regression: the field explorer must name the table after the file, not a
    # generic "csv" — otherwise every CSV connection looks identical in the UI.
    connector = CSVConnector()
    schema = await connector.get_schema({"file_path": csv_file})

    assert len(schema["tables"]) == 1
    assert schema["tables"][0]["name"] == "sales"
    assert [c["name"] for c in schema["tables"][0]["columns"]] == ["region", "revenue"]


async def test_csv_schema_falls_back_for_empty_path():
    connector = CSVConnector()
    schema = await connector.get_schema({"file_path": ""})
    assert schema == {"tables": []}


def _write_xlsx(path: str) -> None:
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_excel(writer, sheet_name="Sheet1", index=False)
        pd.DataFrame({"c": [5]}).to_excel(writer, sheet_name="Data", index=False)


async def test_excel_schema_reports_each_real_sheet(data_root):
    path = str(data_root / "sheets.xlsx")
    _write_xlsx(path)

    schema = await ExcelConnector().get_schema({"file_path": path})
    names = [t["name"] for t in schema["tables"]]

    assert names == ["Sheet1", "Data"]


# --- M1: absolute-path traversal boundary -------------------------------------

from app.services.connectors.csv_excel import (  # noqa: E402
    ExcelQueryError,
    _resolve_file_path,
    _validate_excel_query,
)


async def test_absolute_path_inside_root_is_allowed(data_root):
    """A file inside DATA_ROOT reads fine via an absolute path."""
    target = data_root / "inside.csv"
    target.write_text("a,b\n1,2\n")

    df = await CSVConnector().execute_query({"file_path": str(target)}, "SELECT *")
    assert len(df) == 1


async def test_absolute_path_outside_root_is_blocked(data_root):
    """A file that exists but lives outside DATA_ROOT is rejected (M1)."""
    outside = data_root.parent / "outside.csv"
    outside.write_text("a,b\n1,2\n")

    with pytest.raises(ValueError, match="escapes data directory"):
        await CSVConnector().execute_query({"file_path": str(outside)}, "SELECT *")


def test_resolve_absolute_outside_root_raises(data_root):
    from pathlib import Path

    outside = Path(data_root.parent) / "etc_passwd"
    with pytest.raises(ValueError, match="escapes data directory"):
        _resolve_file_path(str(outside))


async def test_relative_escape_is_blocked(data_root, monkeypatch):
    """Relative paths still cannot escape via ``..`` segments (M1 guard)."""
    monkeypatch.setattr(csv_excel_mod.settings, "DATA_DIR", data_root)

    with pytest.raises(ValueError, match="escapes data directory"):
        _resolve_file_path("../escape.txt")


async def test_excel_absolute_outside_root_blocked(data_root):
    """The Excel connector honours the same boundary (M1)."""
    outside = data_root.parent / "secret.xlsx"
    with pytest.raises(ValueError, match="escapes data directory"):
        await ExcelConnector().execute_query({"file_path": str(outside)}, "SELECT *")


# --- M2: Excel query read-only validation -------------------------------------

import sys as _sys  # noqa: E402
import types as _types  # noqa: E402


def _inject_fake_pandasql(monkeypatch):
    """Inject a fake pandasql module that records the query it receives."""
    captured = {}
    mod = _types.ModuleType("pandasql")

    def fake_sqldf(query, frame):
        captured["query"] = query
        return pd.DataFrame({"q": [query]})

    mod.sqldf = fake_sqldf
    monkeypatch.setitem(_sys.modules, "pandasql", mod)
    return captured


async def test_valid_select_reaches_pandasql(monkeypatch):
    captured = _inject_fake_pandasql(monkeypatch)

    def fake_read_excel(*args, **kwargs):
        return pd.DataFrame({"a": [1, 2], "b": [3, 4]})

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    df = await ExcelConnector().execute_query(
        {"file_path": "dummy.xlsx", "sheet": 0}, "SELECT * WHERE a > 0"
    )
    assert captured["query"] == "SELECT * WHERE a > 0"
    # pandasql received the validated query and returned its (single-row) frame.
    assert len(df) == 1


async def test_select_union_allowed():
    # UNION of SELECTs is still read-only and must pass validation.
    _validate_excel_query("SELECT a FROM x UNION SELECT b FROM y")


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO x VALUES (1)",
        "UPDATE x SET a = 1",
        "DELETE FROM x",
        "DROP TABLE x",
        "CREATE TABLE x (a int)",
        "ALTER TABLE x ADD COLUMN c int",
        "TRUNCATE TABLE x",
        "EXEC xp_cmdshell 'dir'",
        "MERGE INTO x USING y ON (x.a = y.a)",
        "SELECT * FROM x; DROP TABLE x",
        "-- drop table x\nSELECT 1",
        "SELECT 1 /* drop table x */ FROM y",
        "SELECT * # comment\nFROM x",
    ],
)
async def test_dangerous_excel_queries_rejected(query):
    with pytest.raises(ExcelQueryError):
        _validate_excel_query(query)


async def test_dangerous_query_blocked_through_execute_query(monkeypatch):
    """A DML query is rejected end-to-end, not silently passed to pandasql."""
    captured = _inject_fake_pandasql(monkeypatch)

    def fake_read_excel(*args, **kwargs):
        return pd.DataFrame({"a": [1, 2]})

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    with pytest.raises(ExcelQueryError):
        await ExcelConnector().execute_query(
            {"file_path": "dummy.xlsx", "sheet": 0}, "DELETE FROM x"
        )
    # pandasql must never have been reached.
    assert "query" not in captured

