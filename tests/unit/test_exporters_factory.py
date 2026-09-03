"""Tests for the exporter factory / format dispatch (fixes B4: scheduled export
now honors schedule.output_format instead of always emitting PDF).

Native coverage: normalize/mime/extension logic, and CSV + HTML export paths
(which only need pandas). Excel/PDF exporters need xlsxwriter/reportlab, so
their runtime output is verified in Docker, but we can still assert the factory
selects the right class without calling export().
"""

from __future__ import annotations

import pytest

from app.services.exporters import (
    CSVExporter,
    ExcelExporter,
    HTMLExporter,
    export_report,
    get_exporter,
    get_file_extension,
    get_mime_type,
    normalize_output_format,
)

SAMPLE = {
    "name": "Sample",
    "sections": [
        {
            "type": "detail",
            "elements": [
                {
                    "type": "table",
                    "columns": [{"field": "a", "header": "A"}, {"field": "b", "header": "B"}],
                    "data": [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
                }
            ],
        }
    ],
}


def test_normalize_defaults_and_trims():
    assert normalize_output_format(None) == "pdf"
    assert normalize_output_format("  PDF ") == "pdf"
    assert normalize_output_format("HTML") == "html"


@pytest.mark.parametrize(
    "alias,expected",
    [("excel", "xlsx"), ("xls", "xlsx"), ("text/csv", "csv"), ("text/html", "html"), ("htm", "html")],
)
def test_normalize_aliases(alias, expected):
    assert normalize_output_format(alias) == expected


def test_normalize_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_output_format("docx")


def test_get_exporter_selects_class():
    assert isinstance(get_exporter("csv"), CSVExporter)
    assert isinstance(get_exporter("xlsx"), ExcelExporter)  # instantiable without xlsxwriter
    assert isinstance(get_exporter("html"), HTMLExporter)


def test_export_report_csv_returns_bytes():
    result = export_report(SAMPLE, "csv")
    assert isinstance(result, bytes)
    assert b"A,B" in result
    assert b"1,2" in result


def test_export_report_html_normalized_to_bytes():
    # HTMLExporter.export() returns str; export_report must normalize to bytes.
    result = export_report(SAMPLE, "html")
    assert isinstance(result, bytes)
    assert b"<table" in result
    assert result.decode("utf-8").startswith("<!DOCTYPE html>")


@pytest.mark.parametrize(
    "fmt,mime,ext",
    [
        ("pdf", "application/pdf", "pdf"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        ("csv", "text/csv", "csv"),
        ("html", "text/html; charset=utf-8", "html"),
    ],
)
def test_mime_and_extension(fmt, mime, ext):
    assert get_mime_type(fmt) == mime
    assert get_file_extension(fmt) == ext
