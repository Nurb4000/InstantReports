"""Tests for preview HTML assembly helpers."""

from __future__ import annotations

from app.routes.preview import _build_label_html, _build_page_html, _build_section_html


def test_build_section_html_includes_elements():
    html = _build_section_html(
        "detail", "Sales", "<table>data</table>", hide_name=False
    )
    assert "section-detail" in html
    assert "Sales" in html
    assert "<table>data</table>" in html


def test_build_section_html_hides_name_when_requested():
    html = _build_section_html("header", "Title", "<p>content</p>", hide_name=True)
    assert "Title" not in html
    assert "<p>content</p>" in html


def test_build_section_html_shows_placeholder_when_empty():
    html = _build_section_html("footer", "Footer", "", hide_name=False)
    assert "No elements in this section" in html


def test_build_page_html_replaces_placeholders():
    html = _build_page_html("My Report", "A description", "<section>content</section>")
    assert "<title>Preview: My Report</title>" in html
    assert "My Report" in html
    assert "<section>content</section>" in html
    assert "export-toolbar" in html


def test_build_page_html_embeds_definition_json_for_export():
    definition = '{"name": "Sales", "layout": {"sections": []}}'
    html = _build_page_html("Sales", "", "<sections/>", definition_json=definition)
    assert "encodeURIComponent" in html
    assert "/preview/export?definition_json=" in html
    assert "btn-export-pdf').href = baseUrl + 'pdf'" in html
    assert "btn-export-excel').href = baseUrl + 'xlsx'" in html
    assert "btn-export-csv').href = baseUrl + 'csv'" in html


def test_build_page_html_no_def_json_leaves_no_export_links():
    html = _build_page_html("Title", "", "<sections/>")
    assert "var defJson = null" in html
    assert (
        "/preview/export?definition_json=" not in html or "if (!defJson) return" in html
    )


def test_build_page_html_handles_empty_description():
    html = _build_page_html("Title", "", "<sections/>")
    assert "REPORT_DESCRIPTION" not in html
    assert "<sections/>" in html


def test_build_label_html_includes_escaped_label():
    html = _build_label_html("My Label", False)
    assert 'class="element-label"' in html
    assert "My Label" in html


def test_build_label_html_escapes_html():
    html = _build_label_html("<script>alert(1)</script>", False)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_build_label_html_returns_empty_when_hidden():
    assert _build_label_html("Label", True) == ""


def test_build_label_html_returns_empty_when_blank():
    assert _build_label_html("", False) == ""
    assert _build_label_html(None, False) == ""
