from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.services.engine.conditional_formatting import ConditionalFormatter

if TYPE_CHECKING:
    import xlsxwriter


class ExcelExporter:
    """Export reports to Excel using XlsxWriter."""

    def export(self, rendered_report: dict[str, Any]) -> bytes:
        """Export a rendered report to Excel.

        Args:
            rendered_report: The rendered report structure from ReportRenderer

        Returns:
            Excel file bytes
        """
        import xlsxwriter

        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer)

        for section in rendered_report.get("sections", []):
            if section.get("type") == "detail":
                for element in section.get("elements", []):
                    if element.get("type") == "subreport":
                        self._write_subreport(workbook, element)
                    elif element.get("type") == "table":
                        self._write_table(workbook, element)

        workbook.close()
        buffer.seek(0)
        return buffer.read()

    def _write_table(
        self,
        workbook: xlsxwriter.Workbook,
        element: dict[str, Any],
        sheet_name: str | None = None,
    ) -> None:
        """Write a table element to Excel.

        ``sheet_name`` lets callers (e.g. subreport tables) supply a unique name
        so nested tables do not collide on the default "Sheet1".
        """
        data = element.get("data", [])
        columns = element.get("columns", [])

        if not data:
            return

        worksheet_name = sheet_name or element.get("name") or "Sheet1"
        worksheet = workbook.add_worksheet(worksheet_name[:31])
        format_cache: dict[tuple[str, Any], Any] = {}

        headers = [col.get("header", col.get("field", "")) for col in columns]
        worksheet.write_row(0, 0, headers)

        for row_idx, row_data in enumerate(data, start=1):
            formatting = row_data.get("formatting") or {}
            row_format = formatting.get("row") or {}
            row_cell_format = self._get_xlsx_format(workbook, format_cache, row_format) if row_format else None

            for col_idx, col in enumerate(columns):
                field = col.get("field", "")
                value = row_data.get(field, "")
                cell_format = formatting.get("cells", {}).get(field)
                cell_cell_format = self._get_xlsx_format(workbook, format_cache, cell_format) if cell_format else None
                worksheet.write(row_idx, col_idx, value, cell_cell_format or row_cell_format)

    def _write_subreport(self, workbook: xlsxwriter.Workbook, element: dict[str, Any]) -> None:
        """Write a subreport element to Excel.

        Inline subreports recurse into their stored layout and write each nested
        table (mirroring the PDF exporter). Non-inline render modes have no
        embedded content, so we emit a single-cell placeholder rather than
        dropping the subreport silently.
        """
        render_mode = element.get("render_mode") or "inline"
        layout = element.get("layout") or {}
        if render_mode != "inline":
            worksheet_name = ((element.get("label") or "Subreport")[:27] + " Sub")[:31]
            worksheet = workbook.add_worksheet(worksheet_name)
            worksheet.write(0, 0, f"Sub-report ({render_mode}) - content not embedded in Excel export")
            return
        for index, sub_element in enumerate(layout.get("elements", [])):
            if sub_element.get("type") == "table":
                self._write_table(workbook, sub_element, sheet_name=f"Sub{index + 1}")

    @staticmethod
    def _get_xlsx_format(
        workbook: Any, cache: dict[tuple[str, Any], Any], fmt: dict[str, Any]
    ) -> Any:
        """Return (creating on demand) an xlsxwriter format for a formatting dict."""
        key = ("background", fmt.get("background"), "color", fmt.get("color"), "bold", fmt.get("bold"))
        if key not in cache:
            kwargs: dict[str, Any] = {}
            if fmt.get("background"):
                kwargs["bg_color"] = fmt["background"].lstrip("#").upper()
            if fmt.get("color"):
                kwargs["font_color"] = fmt["color"].lstrip("#").upper()
            if fmt.get("bold"):
                kwargs["bold"] = True
            cache[key] = workbook.add_format(kwargs) if kwargs else workbook.add_format()
        return cache[key]


class CSVExporter:
    """Export reports to CSV."""

    def export(self, rendered_report: dict[str, Any]) -> bytes:
        """Export a rendered report to CSV.

        Args:
            rendered_report: The rendered report structure from ReportRenderer

        Returns:
            CSV file bytes
        """
        buffers = []

        for section in rendered_report.get("sections", []):
            if section.get("type") == "detail":
                for element in section.get("elements", []):
                    if element.get("type") == "subreport":
                        self._append_subreport_csv(element, buffers)
                    elif element.get("type") == "table":
                        buffer = self._table_to_csv_buffer(element)
                        if buffer is not None:
                            buffers.append(buffer)

        return "\n".join(buffers).encode("utf-8")

    def _table_to_csv_buffer(self, element: dict[str, Any]) -> bytes | None:
        """Serialize a single table element (top-level or nested) to CSV bytes."""
        data = element.get("data", [])
        if not data:
            return None
        columns = element.get("columns") or []
        if columns:
            fields = [c.get("field", "") for c in columns]
            headers = [c.get("header") or c.get("field") or "" for c in columns]
            df = pd.DataFrame(data, columns=fields)
            df.columns = headers
        else:
            df = pd.DataFrame(data)
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        return csv_buffer.getvalue()

    def _append_subreport_csv(self, element: dict[str, Any], buffers: list[bytes]) -> None:
        """Append an inline subreport's nested tables to the CSV output.

        Non-inline render modes have no embedded content; emit a single-row
        placeholder so the subreport is not dropped silently.
        """
        render_mode = element.get("render_mode") or "inline"
        layout = element.get("layout") or {}
        if render_mode != "inline":
            buffers.append(f"Sub-report ({render_mode}) - content not embedded in CSV export\n")
            return
        for sub_element in layout.get("elements", []):
            if sub_element.get("type") == "table":
                buffer = self._table_to_csv_buffer(sub_element)
                if buffer is not None:
                    buffers.append(buffer)


class HTMLExporter:
    """Export reports to HTML for web viewing."""

    def _row_style(self, row: dict[str, Any]) -> str:
        """Return inline CSS for a row's conditional formatting."""
        formatting = row.get("formatting") or {}
        return ConditionalFormatter().get_css_styles(formatting)

    def _cell_style(self, row: dict[str, Any], field: str) -> str:
        """Return inline CSS for a single cell's conditional formatting."""
        formatting = row.get("formatting") or {}
        cell_format = formatting.get("cells", {}).get(field)
        if not cell_format:
            return ""
        return ConditionalFormatter().get_css_styles({"row": None, "cells": {field: cell_format}})

    def export(self, rendered_report: dict[str, Any]) -> str:
        """Export a rendered report to HTML.

        Args:
            rendered_report: The rendered report structure from ReportRenderer

        Returns:
            HTML string
        """
        html_parts = [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            "<meta charset='UTF-8'>",
            "<title>{}</title>".format(rendered_report.get("name", "Report")),
            "<style>",
            "body { font-family: Arial, sans-serif; margin: 20px; }",
            "table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }",
            "th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }",
            "th { background-color: #f2f2f2; }",
            ".section { margin-bottom: 30px; }",
            "</style>",
            "</head>",
            "<body>",
            "<h1>{}</h1>".format(rendered_report.get("name", "Report")),
        ]

        for section in rendered_report.get("sections", []):
            html_parts.append('<div class="section">')
            html_parts.append("<h2>{}</h2>".format(section.get("type", "").capitalize()))

            for element in section.get("elements", []):
                html_parts.append(self._render_element_html(element))

            html_parts.append("</div>")

        html_parts.append("</body>")
        html_parts.append("</html>")

        return "\n".join(html_parts)

    def _render_element_html(self, element: dict[str, Any]) -> str:
        """Render a single top-level or subreport layout element to HTML.

        Extracted from ``export`` so subreport layouts can reuse the exact same
        text/table/chart rendering instead of dropping subreports silently.
        """
        elem_type = element.get("type")
        if elem_type == "subreport":
            return self._render_subreport_html(element)
        if elem_type == "text":
            return "<p>{}</p>".format(element.get("content", ""))
        if elem_type == "table":
            return self._render_table_html(element)
        if elem_type == "chart":
            return self._render_chart_html(element)
        # Unknown element types render as a muted note rather than vanishing.
        return '<p class="text-muted">Unsupported element: {}'.format(elem_type or "?") + "</p>"

    def _render_table_html(self, element: dict[str, Any]) -> str:
        """Render a table element (top-level or nested in a subreport) to HTML."""
        data = element.get("data", [])
        columns = element.get("columns", [])
        if not data:
            return '<p class="text-muted">Empty table.</p>'
        parts = ["<table>", "<thead><tr>"]
        for col in columns:
            header = col.get("header", col.get("field", ""))
            parts.append(f"<th>{header}</th>")
        parts.append("</tr></thead><tbody>")
        for row in data:
            row_style = self._row_style(row)
            parts.append(f'<tr style="{row_style}">' if row_style else "<tr>")
            for col in columns:
                field = col.get("field", "")
                value = row.get(field, "")
                cell_style = self._cell_style(row, field)
                tag_open = f'<td style="{cell_style}">' if cell_style else "<td>"
                parts.append(f"{tag_open}{value}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
        return "".join(parts)

    def _render_subreport_html(self, element: dict[str, Any]) -> str:
        """Render a subreport element.

        Inline subreports recurse into their stored layout and render each child
        element with the same logic as top-level elements. Other render modes
        (drill_down/page/detached) have no embedded content, so we surface a
        placeholder instead of dropping the subreport silently.
        """
        render_mode = element.get("render_mode") or "inline"
        layout = element.get("layout") or {}
        if render_mode != "inline":
            return (
                '<div class="subreport-placeholder" '
                'style="border:1px dashed #ccc; padding:8px; margin:8px 0; '
                f'color:#666;">Sub-report ({render_mode}) — content not embedded in HTML export'
                + "</div>"
            )
        body = '<div class="subreport" style="margin:8px 0;">'
        for sub_element in layout.get("elements", []):
            body += self._render_element_html(sub_element)
        return body + "</div>"

    def _render_chart_html(self, element: dict[str, Any]) -> str:
        """Render a chart element as an inline base64 PNG.

        Mirrors the PDF exporter's behaviour so HTML (the "view in browser"
        format) does not silently drop charts the way Excel/CSV do.
        """
        import base64

        from app.services.engine.chart import ChartGenerator

        chart_data = element.get("data")
        if chart_data is None or len(chart_data) == 0:
            return '<p class="text-muted">No data for chart.</p>'
        try:
            png_bytes = ChartGenerator().generate(element, chart_data)
        except Exception as exc:
            return f'<p class="text-danger">Chart failed to render: {exc}</p>'
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return (
            '<div class="report-chart" style="text-align:center; margin:12px 0;">'
            f'<img src="data:image;base64,{b64}" style="max-width:100%; height:auto;" alt="chart"/>'
            "</div>"
        )
