"""Unit tests for delivery helpers that don't require external mail/DB deps.

The SMTP send/test paths need ``aiosmtplib`` (not installed in the test env), but
``_get_mime_subtype`` is pure logic that decides the MIME subtype used when
attaching a report file. A wrong subtype means the attachment may not open in the
recipient's viewer, so it deserves direct coverage.
"""
from __future__ import annotations

import asyncio
import sys
import types

from app.services.delivery.email import _get_mime_subtype, send_email


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


def _inject_fake_aiosmtplib():
    """Provide a fake ``aiosmtplib`` with an async ``send`` that captures the msg."""
    captured: dict[str, object] = {}

    async def fake_send(msg, **kwargs):
        captured["msg"] = msg

    mod = types.ModuleType("aiosmtplib")
    mod.send = fake_send
    previous = sys.modules.get("aiosmtplib")
    sys.modules["aiosmtplib"] = mod
    return captured, previous


def _run_send(attachment_filename: str) -> object:
    captured, previous = _inject_fake_aiosmtplib()
    try:
        asyncio.run(
            send_email(
                smtp_host="h",
                smtp_port=25,
                smtp_user="",
                smtp_password="",
                smtp_from="a@b.com",
                to_emails=["x@y.com"],
                subject="s",
                body="b",
                attachment=b"data",
                attachment_filename=attachment_filename,
            )
        )
    finally:
        if previous is None:
            sys.modules.pop("aiosmtplib", None)
        else:
            sys.modules["aiosmtplib"] = previous
    return captured["msg"]


def _attachment_content_type(msg: object) -> str:
    """Navigate the multipart/mixed wrapper to find the attachment's MIME type."""
    payload = msg.get_payload()  # type: ignore[attr-defined]
    if isinstance(payload, list):
        for part in payload:
            if part.get_filename():  # type: ignore[attr-defined]
                return part.get_content_type()  # type: ignore[attr-defined]
    raise AssertionError("no attachment part found")


def test_email_csv_attachment_uses_text_mime():
    # Regression: CSV was force-wrapped as application/csv; clients can't open it.
    assert _attachment_content_type(_run_send("report.csv")) == "text/csv"


def test_email_html_attachment_uses_text_mime():
    assert _attachment_content_type(_run_send("report.html")) == "text/html"


def test_email_pdf_attachment_stays_application_mime():
    # Ensure the fix doesn't over-correct valid application/* types.
    assert _attachment_content_type(_run_send("report.pdf")) == "application/pdf"


def test_email_xlsx_attachment_keeps_application_mime():
    assert _attachment_content_type(_run_send("report.xlsx")) == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
