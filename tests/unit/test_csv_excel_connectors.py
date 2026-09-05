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

from app.services.connectors.csv_excel import CSVConnector, ExcelConnector


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "sales.csv"
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


async def test_excel_schema_reports_each_real_sheet(tmp_path):
    path = str(tmp_path / "sheets.xlsx")
    _write_xlsx(path)

    schema = await ExcelConnector().get_schema({"file_path": path})
    names = [t["name"] for t in schema["tables"]]

    assert names == ["Sheet1", "Data"]
