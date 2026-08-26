from __future__ import annotations

import io
from typing import Any

import pandas as pd
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

        headers = [col.get("header", col.get("field", "")) for col in columns]
        worksheet.write_row(0, 0, headers)

        for row_idx, row_data in enumerate(data, start=1):
            for col_idx, col in enumerate(columns):
                field = col.get("field", "")
                value = row_data.get(field, "")
                worksheet.write(row_idx, col_idx, value)


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
                            html_parts.append("<tr>")
                            for col in columns:
                                field = col.get("field", "")
                                value = row.get(field, "")
                                html_parts.append("<td>{}</td>".format(value))
                            html_parts.append("</tr>")
                        html_parts.append("</tbody>")
                        html_parts.append("</table>")

            html_parts.append("</div>")

        html_parts.append("</body>")
        html_parts.append("</html>")

        return "\n".join(html_parts)
