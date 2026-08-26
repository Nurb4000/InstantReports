from __future__ import annotations

import io
import uuid
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.engine.chart import ChartGenerator


class PDFExporter:
    """Export reports to PDF using ReportLab."""

    def __init__(self):
        self.chart_generator = ChartGenerator()

    def export(self, rendered_report: dict[str, Any], output_format: str = "pdf") -> bytes:
        """Export a rendered report to PDF.

        Args:
            rendered_report: The rendered report structure from ReportRenderer
            output_format: Output format (currently only 'pdf')

        Returns:
            PDF file bytes
        """
        page_size = A4 if rendered_report.get("metadata", {}).get("page", {}).get("size") == "A4" else letter
        orientation = rendered_report.get("metadata", {}).get("page", {}).get("orientation", "portrait")

        if orientation == "landscape":
            from reportlab.lib.pagesizes import landscape
            page_size = landscape(page_size)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            rightMargin=cm(2),
            leftMargin=cm(2),
            topMargin=cm(2),
            bottomMargin=cm(2),
        )

        styles = getSampleStyleSheet()
        story = []

        for section in rendered_report.get("sections", []):
            self._render_section(story, section, styles)

        doc.build(story)
        buffer.seek(0)
        return buffer.read()

    def _render_section(
        self, story: list, section: dict[str, Any], styles: dict[str, Any]
    ) -> None:
        """Render a section (header, detail, summary, footer)."""
        section_type = section.get("type", "detail")

        if section_type == "header":
            for element in section.get("elements", []):
                self._render_element(story, element, styles)
            story.append(Spacer(1, 0.25 * inch))

        elif section_type == "detail":
            for element in section.get("elements", []):
                self._render_element(story, element, styles)
            story.append(Spacer(1, 0.25 * inch))

        elif section_type == "summary":
            for element in section.get("elements", []):
                self._render_element(story, element, styles)
            story.append(Spacer(1, 0.25 * inch))

        elif section_type == "footer":
            story.append(Spacer(1, 0.5 * inch))
            for element in section.get("elements", []):
                self._render_element(story, element, styles)

    def _render_element(
        self, story: list, element: dict[str, Any], styles: dict[str, Any]
    ) -> None:
        """Render a single element."""
        element_type = element.get("type", "text")

        if element_type == "text":
            content = element.get("content", "")
            style_name = element.get("style", "Normal")
            style = styles.get(style_name, styles["Normal"])
            story.append(Paragraph(content, style))
            story.append(Spacer(1, 0.1 * inch))

        elif element_type == "table":
            self._render_table(story, element, styles)

        elif element_type == "chart":
            self._render_chart(story, element)

        elif element_type == "image":
            source = element.get("source", "")
            if source.startswith("http"):
                from reportlab.lib.utils import ImageReader
                img = ImageReader(source)
            else:
                img = Image(source)
            story.append(img)

        elif element_type == "subreport":
            self._render_subreport(story, element, styles)

    def _render_table(
        self, story: list, element: dict[str, Any], styles: dict[str, Any]
    ) -> None:
        """Render a table element."""
        data = element.get("data", [])
        columns = element.get("columns", [])

        if not data:
            return

        headers = [col.get("header", col.get("field", "")) for col in columns]
        table_data = [headers] + [[str(row.get(col.get("field", ""), "")) for col in columns] for row in data]

        table = Table(table_data)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ]))

        story.append(table)
        story.append(Spacer(1, 0.25 * inch))

    def _render_chart(self, story: list, element: dict[str, Any]) -> None:
        """Render a chart element."""
        data_source_id = element.get("data_source")
        if not data_source_id:
            return

        try:
            chart_bytes = self.chart_generator.generate(element, pd.DataFrame())
            from reportlab.lib.utils import ImageReader
            img = ImageReader(io.BytesIO(chart_bytes))
            story.append(img)
            story.append(Spacer(1, 0.25 * inch))
        except Exception:
            pass

    def _render_subreport(
        self, story: list, element: dict[str, Any], styles: dict[str, Any]
    ) -> None:
        """Render a subreport element."""
        render_mode = element.get("render_mode", "inline")

        if render_mode == "inline":
            layout = element.get("layout", {})
            for sub_element in layout.get("elements", []):
                self._render_element(story, sub_element, styles)

        elif render_mode == "drill_down":
            story.append(Paragraph("Drill-down report (click to expand)", styles["Normal"]))
