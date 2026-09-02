"""Unit tests for delivery helpers that don't require external mail/DB deps.

The SMTP send/test paths need ``aiosmtplib`` (not installed in the test env), but
``_get_mime_subtype`` is pure logic that decides the MIME subtype used when
attaching a report file. A wrong subtype means the attachment may not open in the
recipient's viewer, so it deserves direct coverage.
"""
from __future__ import annotations

from app.services.delivery.email import _get_mime_subtype


def test_pdf():
    assert _get_mime_subtype("report.pdf") == "pdf"


def test_excel_variants():
    assert _get_mime_subtype("q1.xlsx") == "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert _get_mime_subtype("q1.XLSX") == "vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_xls():
    assert _get_mime_subtype("legacy.xls") == "vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_csv():
    assert _get_mime_subtype("data.csv") == "csv"


def test_html_variants():
    assert _get_mime_subtype("report.html") == "html"
    assert _get_mime_subtype("report.htm") == "html"


def test_unknown_falls_back_to_octet_stream():
    assert _get_mime_subtype("report.dat") == "octet-stream"
    assert _get_mime_subtype("no_extension") == "octet-stream"
