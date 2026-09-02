from __future__ import annotations

import io
import logging
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm, inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.utils import Image

from app.services.engine.chart import ChartGenerator

logger = logging.getLogger(__name__)



class PDFExporter:
    """Export reports to PDF using ReportLab."""

    def __init__(self):
        self.chart_generator = ChartGenerator()
        self.renderer = None  # Will be set when exporting

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
        
        # Create frames for header, body, and footer
        left_margin = cm(2)
        right_margin = cm(2)
        top_margin = cm(2)
        bottom_margin = cm(2)
        
        body_height = page_size[1] - top_margin - bottom_margin - inch
        
        left_frame = Frame(
            left_margin, bottom_margin,
            page_size[0] - left_margin - right_margin,
            body_height,
            id='normal'
        )
        
        # Create document template
        doc = BaseDocTemplate(
            buffer,
            pagesize=page_size,
            title=rendered_report.get("name", "Report"),
            author="InstantReports",
        )
        
        # Define page templates with header/footer
        def header(canvas, doc):
            canvas.saveState()
            canvas.setFont('Helvetica', 8)
            canvas.drawString(left_margin, page_size[1] - top_margin + cm(0.5), 
                            rendered_report.get("name", ""))
            canvas.restoreState()

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont('Helvetica', 8)
            page_num = canvas.getPageNumber()
            text = f"Page {page_num}"
            canvas.drawCentredString(page_size[0] / 2.0, bottom_margin - cm(1), text)
            canvas.restoreState()

        # Create page template and add to document
        page_template = PageTemplate(
            id='MainTemplate',
            frames=[left_frame],
            onPage=footer,
        )
        doc.addPageTemplates([page_template])
        
        styles = getSampleStyleSheet()
        story = []

        # Add title as first element
        title_style = styles['Title']
        story.append(Paragraph(rendered_report.get("name", "Report"), title_style))
        story.append(Spacer(1, 0.5 * inch))

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
        element_label = element.get("label", "")

        # Add label if present
        if element_label:
            label_style = styles.get("Normal", styles["Normal"])
            story.append(Paragraph(element_label, label_style))
            story.append(Spacer(1, 0.1 * inch))

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

        table_style = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ])
        self._apply_conditional_formatting(table_style, data, columns)

        table = Table(table_data)
        table.setStyle(table_style)

        story.append(table)
        story.append(Spacer(1, 0.25 * inch))

    @staticmethod
    def _apply_conditional_formatting(table_style: TableStyle, data: list[dict[str, Any]], columns: list[dict[str, Any]]) -> None:
        """Append conditional-formatting directives to a table style."""
        for row_idx, row in enumerate(data):
            formatting = row.get("formatting") or {}
            row_format = formatting.get("row") or {}
            base_row = row_idx + 1  # row 0 is the header

            if row_format:
                if row_format.get("background"):
                    table_style.add("BACKGROUND", (0, base_row), (-1, base_row), colors.HexColor(row_format["background"]))
                if row_format.get("color"):
                    table_style.add("TEXTCOLOR", (0, base_row), (-1, base_row), colors.HexColor(row_format["color"]))
                if row_format.get("bold"):
                    table_style.add("FONTNAME", (0, base_row), (-1, base_row), "Helvetica-Bold")

            for col_idx, col in enumerate(columns):
                field = col.get("field")
                cell_format = formatting.get("cells", {}).get(field)
                if not cell_format:
                    continue
                if cell_format.get("background"):
                    table_style.add("BACKGROUND", (col_idx, base_row), (col_idx, base_row), colors.HexColor(cell_format["background"]))
                if cell_format.get("color"):
                    table_style.add("TEXTCOLOR", (col_idx, base_row), (col_idx, base_row), colors.HexColor(cell_format["color"]))
                if cell_format.get("bold"):
                    table_style.add("FONTNAME", (col_idx, base_row), (col_idx, base_row), "Helvetica-Bold")

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
        except Exception as e:
            logger.error("Failed to render chart in PDF: %s", e)

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
