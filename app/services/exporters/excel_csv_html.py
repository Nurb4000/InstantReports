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
                    if element.get("type") == "table":
                        self._write_table(workbook, element)

        workbook.close()
        buffer.seek(0)
        return buffer.read()

    def _write_table(self, workbook: xlsxwriter.Workbook, element: dict[str, Any]) -> None:
        """Write a table element to Excel."""
        data = element.get("data", [])
        columns = element.get("columns", [])

        if not data:
            return

        worksheet = workbook.add_worksheet(element.get("name", "Sheet1")[:31])
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
                    if element.get("type") == "table":
                        data = element.get("data", [])
                        if data:
                            df = pd.DataFrame(data)
                            csv_buffer = io.StringIO()
                            df.to_csv(csv_buffer, index=False)
                            buffers.append(csv_buffer.getvalue())

        return "\n".join(buffers).encode("utf-8")


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
                if element.get("type") == "text":
                    html_parts.append("<p>{}</p>".format(element.get("content", "")))
                elif element.get("type") == "table":
                    data = element.get("data", [])
                    columns = element.get("columns", [])
                    if data:
                        html_parts.append("<table>")
                        html_parts.append("<thead><tr>")
                        for col in columns:
                            header = col.get("header", col.get("field", ""))
                            html_parts.append("<th>{}</th>".format(header))
                        html_parts.append("</tr></thead>")
                        html_parts.append("<tbody>")
                        for row in data:
                            row_style = self._row_style(row)
                            row_open = '<tr style="{}">'.format(row_style) if row_style else "<tr>"
                            html_parts.append(row_open)
                            for col in columns:
                                field = col.get("field", "")
                                value = row.get(field, "")
                                cell_style = self._cell_style(row, field)
                                tag_open = '<td style="{}">'.format(cell_style) if cell_style else "<td>"
                                html_parts.append("{}{}</td>".format(tag_open, value))
                            html_parts.append("</tr>")
                        html_parts.append("</tbody>")
                        html_parts.append("</table>")

            html_parts.append("</div>")

        html_parts.append("</body>")
        html_parts.append("</html>")

        return "\n".join(html_parts)
