from __future__ import annotations

import html
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.report import Report
from app.models.user import User
from app.routes.auth import get_current_user_optional

logger = logging.getLogger(__name__)
router = APIRouter()

TABLE_ROW_LIMIT = settings.PREVIEW_TABLE_ROW_LIMIT
CHART_ROW_LIMIT = settings.PREVIEW_CHART_ROW_LIMIT


@dataclass
class _PreviewContext:
    """Shared state for preview rendering: connector, db session, definition."""

    connector: Any = None
    connection_config: dict | None = None
    db: AsyncSession | None = None
    definition: dict = field(default_factory=dict)
    log: logging.Logger = field(default_factory=lambda: logger)


def _build_label_html(elem_label: str, hide_label: bool) -> str:
    """Build the label div HTML for a report element.

    Returns an empty string when the label is blank or hidden.
    """
    if not elem_label or hide_label:
        return ""
    return (
        f'<div class="element-label" style="font-weight: bold; margin-bottom: 5px; '
        f'color: #333; font-size: 14px;">{html.escape(str(elem_label))}</div>'
    )


@router.get("/temp")
async def preview_temp(
    request: Request,
    definition_json: str = Query(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Generate a temporary preview from a definition (no save required)."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Arbitrary-query preview tool: restrict to admin/designer so low-privilege
    # users cannot submit a definition whose element queries execute against an
    # application data-source connection. Mirrors the role gate in preview_report.
    from app.routes._auth_helpers import get_role_value

    if get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        definition = json.loads(definition_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    title = definition.get("name", "Temporary Preview")
    description = definition.get("description", "")

    html_content = await render_report_with_data(
        definition, title, description, db, definition_json
    )
    return HTMLResponse(content=html_content)


@router.get("/export")
async def preview_export(
    request: Request,
    definition_json: str = Query(...),
    format: str = Query("pdf"),
    name: str = Query("", description="Optional download filename base."),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Export a temporary report definition (from designer) as a file download."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Same role gate as preview_temp: arbitrary-query export must stay in the
    # hands of admins/designers.
    from app.routes._auth_helpers import get_role_value

    if get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        definition = json.loads(definition_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    from app.services.exporters import (
        get_file_extension,
        get_mime_type,
        normalize_output_format,
    )
    from app.services.report.rendering import fetch_element_data, render_report_bytes

    try:
        fmt = normalize_output_format(format)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format: {format!r}. Use one of: pdf, xlsx, csv, html.",
        )

    try:
        element_data = await fetch_element_data(
            definition,
            db,
            parameters=definition.get("parameters"),
            label=definition.get("name", "report"),
        )
        report_bytes = render_report_bytes(definition, element_data, fmt)
    except Exception as e:
        logger.exception("Temporary preview export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e!s}")

    filename_base = name or definition.get("name") or "report"
    safe_base = (
        "".join(
            c if c.isalnum() or c in ("-", "_", ".") else "_" for c in filename_base
        ).strip()
        or "report"
    )

    return Response(
        content=report_bytes,
        media_type=get_mime_type(fmt),
        headers={
            "Content-Disposition": f'attachment; filename="{safe_base}.{get_file_extension(fmt)}"'
        },
    )


@router.get("/{report_id}")
async def preview_report(
    request: Request,
    report_id: uuid.UUID,
    format: str = "html",
    use_current: bool = False,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Generate a preview of a report (HTML or PDF)."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get the report
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Check authorization
    from app.routes._auth_helpers import get_role_value

    role = get_role_value(current_user)
    if role not in ("admin", "designer") and report.created_by != current_user.id:
        # Non-designers can only preview their own reports
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        # If use_current is true, we'll need to get the current canvas state from the request
        # For now, just use the saved definition
        import json

        definition = report.definition
        definition_json = json.dumps(definition)
        title = report.name
        description = report.description or ""

        html_content = await render_report_with_data(
            definition,
            title,
            description,
            db=db,
            definition_json=definition_json,
        )

        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {e!s}")


def _build_section_html(
    section_type: str, section_name: str, elements_html: str, hide_name: bool
) -> str:
    """Build the HTML for a single report section (header + body)."""
    header_html = ""
    if not hide_name:
        header_html = f"""            <div class="section-header" style="background: #f8f9fa; padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">
                {section_name}
            </div>
        """
    return f"""
        <div class="report-section section-{section_type.lower()}" style="page-break-inside: avoid; margin-bottom: 20px; border: 1px solid #ddd; padding: 10px;">
            {header_html}
            <div class="section-body" style="padding: 10px;">
                {elements_html if elements_html else '<div style="color: #999; font-style: italic;">No elements in this section</div>'}
            </div>
        </div>
        """


def _build_page_html(
    title: str, description: str, sections_html: str, definition_json: str = ""
) -> str:
    """Assemble the final HTML page with export toolbar, sections, and footer."""
    # Embed definition as a real JS string literal (json.dumps output is valid
    # JS). HTML-escaped entities would NOT be decoded inside <script>, so the
    # older html.escape approach would corrupt the JSON.
    if definition_json:
        js_def = json.dumps(definition_json)
        # Neutralize </script> sequence and HTML-sensitive chars in the literal.
        js_def = js_def.replace("</", "<\\/").replace("<!--", "<\\!--")
    else:
        js_def = "null"
    js_title = json.dumps(title or "")
    page_html = """<!DOCTYPE html>
<html>
<head>
    <title>Preview: REPORT_TITLE</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }
        .report-container {
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .export-toolbar {
            display: flex;
            justify-content: flex-end;
            gap: 8px;
            padding-bottom: 12px;
            margin-bottom: 16px;
            border-bottom: 1px solid #e0e0e0;
        }
        .export-toolbar a {
            display: inline-block;
            padding: 6px 14px;
            font-size: 13px;
            font-family: Arial, sans-serif;
            color: #fff;
            background: #0d6efd;
            border: none;
            border-radius: 4px;
            text-decoration: none;
            cursor: pointer;
        }
        .export-toolbar a:hover {
            background: #0b5ed7;
        }
        .export-toolbar a.btn-excel {
            background: #198754;
        }
        .export-toolbar a.btn-excel:hover {
            background: #157347;
        }
        .export-toolbar a.btn-csv {
            background: #6c757d;
        }
        .export-toolbar a.btn-csv:hover {
            background: #5a6268;
        }
        .report-section {
            background: white;
        }
        .section-header {
            background: #e9ecef;
        }
        .section-detail .section-header {
            background: #f8f9fa;
        }
        .section-summary .section-header {
            background: #e2e3e5;
        }
        .section-footer .section-header {
            background: #dee2e6;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background: #f8f9fa;
            font-weight: bold;
        }
        tr:nth-child(even) {
            background: #f9f9f9;
        }
        @media print {
            body { background: white; }
            .report-container { box-shadow: none; }
            .export-toolbar { display: none; }
        }
    </style>
</head>
<body>
    <div class="report-container">
        <div class="export-toolbar" id="export-toolbar">
            <a id="btn-export-pdf" href="#">PDF</a>
            <a id="btn-export-excel" class="btn-excel" href="#">Excel</a>
            <a id="btn-export-csv" class="btn-csv" href="#">CSV</a>
        </div>
        
        <div class="report-body">
            SECTIONS_HTML
        </div>
    </div>
    <script>
    (function() {
        var defJson = EXPORT_DEF_JSON;
        var reportName = EXPORT_REPORT_NAME;
        if (!defJson) return;
        var baseUrl = '/preview/export?definition_json=' + encodeURIComponent(defJson)
            + '&name=' + encodeURIComponent(reportName) + '&format=';
        document.getElementById('btn-export-pdf').href = baseUrl + 'pdf';
        document.getElementById('btn-export-excel').href = baseUrl + 'xlsx';
        document.getElementById('btn-export-csv').href = baseUrl + 'csv';
    })();
    </script>
</body>
</html>"""

    page_html = page_html.replace("REPORT_TITLE", title)
    page_html = page_html.replace("EXPORT_DEF_JSON", js_def)
    page_html = page_html.replace("EXPORT_REPORT_NAME", js_title)
    page_html = page_html.replace("SECTIONS_HTML", sections_html)

    return page_html


async def render_report_with_data(
    definition: dict,
    title: str,
    description: str = "",
    db=None,
    definition_json: str = "",
) -> str:
    """Render a report definition to HTML with actual data from database."""

    from app.services.connectors.base import resolve_data_source_connector
    from app.services.engine.data_processor import DataProcessor

    # Resolve the primary data source connector + config once and reuse it for
    # every element. Connector type comes from the model column, so non-
    # PostgreSQL sources (MySQL/SQL Server/REST/...) use the right connector.
    data_sources = definition.get("data_sources", [])
    connector, connection_config = await resolve_data_source_connector(db, definition)

    logger.info(f"Data sources in definition: {len(data_sources)}")
    if connector is None:
        logger.warning("No usable data connection found for preview")

    sections_html = ""
    for section in definition.get("layout", {}).get("sections", []):
        section_type = section.get("type", "detail")
        section_name = section.get("custom_name", section_type.capitalize())
        elements_html = ""

        for element in section.get("elements", []):
            # Ensure clean transaction state before each element
            if db:
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
            elem_type = element.get("type", "text")
            props = element.get("properties", {})
            elem_label = element.get("label", "")
            hide_label = element.get("hide_label", False)
            logger.info(
                f"Processing element: type={elem_type}, has_query={'query' in props}"
            )

            label_html = _build_label_html(elem_label, hide_label)

            if elem_type == "text":
                content = props.get("content", "")
                font_size = props.get("fontSize", 12)
                bold = props.get("bold", False)
                color = props.get("color", "#000000")
                elements_html += f"""
                {label_html}
                <div class="report-element text-element" style="font-size: {html.escape(str(font_size))}px; font-weight: {"bold" if bold else "normal"}; color: {html.escape(str(color))}; padding: 5px; border: 1px dashed #ccc; margin: 5px 0;">
                    {html.escape(str(content), quote=True)}
                </div>
                """
            elif elem_type == "table":
                query = props.get("query", "")

                # Try to execute the query using the resolved data source connector
                if query and connection_config and connector:
                    try:
                        # Execute query using the connector
                        df = await connector.execute_query(connection_config, query)

                        if df is None or len(df) == 0:
                            elements_html += '<div style="padding: 10px; color: #999;">No data returned</div>'
                            continue

                        # Commit after query to avoid transaction issues
                        await db.commit()

                        if df is not None and len(df) > 0:
                            # Apply report-level calculated fields (and grouping) before rendering
                            try:
                                df = DataProcessor().process(df, definition)
                            except Exception as processing_error:
                                logger.warning(
                                    f"Calculated field processing failed: {processing_error}"
                                )

                            # Convert DataFrame to HTML table. Honor the element's configured
                            # column headers so the preview matches export; fall back to raw
                            # df columns for query-only tables with no configured columns.
                            configured_cols = element.get("columns") or []
                            if configured_cols:
                                fields = [c.get("field", "") for c in configured_cols]
                                headers = [
                                    c.get("header") or c.get("field") or ""
                                    for c in configured_cols
                                ]
                            else:
                                fields = [str(c) for c in df.columns]
                                headers = list(fields)
                            rows = df.head(TABLE_ROW_LIMIT).to_dict("records")

                            # Apply conditional formatting rules if defined
                            formatting_rules = props.get("formatting_rules") or []
                            formatted_rows = rows
                            cf = None
                            if formatting_rules:
                                try:
                                    from app.services.engine.conditional_formatting import (
                                        ConditionalFormatter,
                                    )

                                    cf = ConditionalFormatter()
                                    formatted_rows = cf.apply_rules(
                                        rows, formatting_rules, df
                                    )
                                except Exception as formatting_error:
                                    logger.warning(
                                        f"Conditional formatting failed: {formatting_error}"
                                    )
                                    formatted_rows = rows

                            # Build table HTML. Display configured headers; look up cell values
                            # and per-cell formatting by field so they stay aligned with export.
                            th_cells = "".join(
                                '<th style="border: 1px solid #ddd; padding: 8px; text-align: left;">'
                                + html.escape(str(header))
                                + "</th>"
                                for header in headers
                            )
                            td_rows = ""
                            for row in formatted_rows:
                                fmt = row.get("formatting") or {}
                                row_css = cf.get_css_styles(fmt) if cf else ""
                                tr_style = (
                                    f' style="{html.escape(str(row_css))}"'
                                    if row_css
                                    else ""
                                )
                                td_cells = ""
                                for field, header in zip(fields, headers):
                                    cell_fmt = fmt.get("cells", {}).get(field)
                                    cell_style = ""
                                    if cf is not None and cell_fmt:
                                        cell_css = cf.get_css_styles(
                                            {"row": None, "cells": {field: cell_fmt}}
                                        )
                                        if cell_css:
                                            cell_style = (
                                                f' style="{html.escape(str(cell_css))}"'
                                            )
                                    td_cells += (
                                        '<td style="border: 1px solid #ddd; padding: 6px;">'
                                        + cell_style
                                        + html.escape(
                                            str(row.get(field, "")), quote=True
                                        )
                                        + "</td>"
                                    )
                                td_rows += "<tr" + tr_style + ">" + td_cells + "</tr>\n"

                            table_html = f"""
                            <div class="report-element table-element" style="padding: 5px; border: 1px solid #ddd; margin: 5px 0; overflow-x: auto;">
                                <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
                                    <thead>
                                        <tr style="background: #f8f9fa;">
                                            {th_cells}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {td_rows}
                                    </tbody>
                                </table>
                                <div style="font-size: 10px; color: #999; margin-top: 5px;">
                                    Showing {min(len(rows), 50)} of {len(df)} rows
                                </div>
                            </div>
                            """
                            elements_html += table_html
                        else:
                            elements_html += '<div style="padding: 10px; color: #999;">No data returned</div>'
                    except Exception as e:
                        logger.error(f"Query execution failed: {e}")
                        elements_html += f"""
                        <div class="report-element table-element" style="padding: 5px; border: 1px dashed #ccc; margin: 5px 0; color: #dc3545;">
                            <div style="font-weight: bold;">Query Error</div>
                            <div style="font-size: 11px;">{str(e)[:200]}</div>
                        </div>
                        """
                else:
                    # No database connection, show query as text
                    elements_html += f"""
                    {label_html}
                    <div class="report-element table-element" style="padding: 5px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 5px;">Table Element (Preview Mode)</div>
                        <pre style="font-size: 11px; background: #f8f9fa; padding: 5px; overflow: auto; max-height: 200px;">{query}</pre>
                    </div>
                    """
            elif elem_type == "image":
                img_src = props.get("src", "")
                img_alt = props.get("alt", "Image")
                img_width = props.get("width", 200)
                img_height = props.get("height", 150)
                elements_html += f'''
                {label_html}
                <div class="report-element image-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0; text-align: center;">
                    <img src="{img_src}" alt="{img_alt}" style="max-width: {img_width}px; max-height: {img_height}px;" />
                </div>
                '''
            elif elem_type == "chart":
                chart_type = props.get("type", "bar")
                chart_title = props.get("title", "Chart")
                x_field = props.get("xField", "")
                y_field = props.get("yField", "")

                # Execute query for chart data using connector (same as tables)
                chart_data = []
                if connection_config and connector:
                    try:
                        import logging

                        logger_chart = logging.getLogger(__name__)

                        query = props.get("query", "")
                        if query:
                            logger_chart.info(
                                f"Executing chart query using connector: {query[:100]}..."
                            )
                            df = await connector.execute_query(connection_config, query)

                            if df is not None and len(df) > 0:
                                # Convert DataFrame to list of dicts for chart rendering
                                chart_data = df.head(10).to_dict(orient="records")
                                logger_chart.info(
                                    f"Chart query returned {len(chart_data)} rows"
                                )
                    except Exception as e:
                        logger.error(
                            f"Failed to execute chart query via connector: {e}"
                        )
                        import traceback

                        logger.error(traceback.format_exc())

                # Coerce all numeric values to float (PostgreSQL returns Decimal)
                for row in chart_data:
                    for k, v in row.items():
                        if v is not None and not isinstance(v, str):
                            try:
                                row[k] = float(v)
                            except (TypeError, ValueError):
                                pass

                # Render chart based on type
                if chart_type == "pie" and chart_data:
                    values = [d.get(y_field, 0) or 0 for d in chart_data]
                    total = sum(values)
                    colors = [
                        "#3498db",
                        "#e74c3c",
                        "#2ecc71",
                        "#f39c12",
                        "#9b59b6",
                        "#1abc9c",
                        "#e67e22",
                        "#34495e",
                    ]

                    slices_html = ""
                    for i, item in enumerate(chart_data):
                        label = item.get(x_field, "N/A") if x_field else "N/A"
                        value = item.get(y_field) or 0
                        percentage = (value / total * 100) if total > 0 else 0
                        color = colors[i % len(colors)]

                        slices_html += f"""
                        <div style="display: flex; align-items: center; margin-bottom: 8px;">
                            <div style="width: 20px; height: 20px; background: {color}; border-radius: 3px; margin-right: 10px;"></div>
                            <div style="flex: 1;">
                                <div style="font-size: 12px; font-weight: 500;">{html.escape(str(label), quote=True)}</div>
                                <div style="font-size: 11px; color: #666;">${value:,.2f} ({percentage:.1f}%)</div>
                            </div>
                        </div>
                        """

                    svg_slices = ""
                    start_angle = -90.0
                    cx, cy, r = 100.0, 100.0, 80.0
                    total_f = float(total) if total else 0.0
                    for i, item in enumerate(chart_data):
                        val = float(item.get(y_field) or 0)
                        pct = (val / total_f) if total_f > 0 else 0
                        sweep = pct * 360.0
                        end_angle = start_angle + sweep
                        large_arc = 1 if sweep > 180 else 0
                        x1 = cx + r * math.cos(math.radians(start_angle))
                        y1 = cy + r * math.sin(math.radians(start_angle))
                        x2 = cx + r * math.cos(math.radians(end_angle))
                        y2 = cy + r * math.sin(math.radians(end_angle))
                        if pct >= 1:
                            d = f"M {cx},{cy} L {x1:.2f},{y1:.2f} A {r},{r} 0 1,1 {x2:.2f},{y2:.2f} Z"
                        else:
                            d = f"M {cx},{cy} L {x1:.2f},{y1:.2f} A {r},{r} 0 {large_arc},1 {x2:.2f},{y2:.2f} Z"
                        svg_slices += f'<path d="{d}" fill="{colors[i % len(colors)]}" stroke="white" stroke-width="1"/>'
                        start_angle = end_angle

                    pie_svg = f"""<svg viewBox="0 0 200 200" width="200" height="200" style="display:block;margin:0 auto;">{svg_slices}</svg>"""

                    chart_html = f"""
                    {label_html}
                    <div class="report-element chart-element" style="padding: 15px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 15px; text-align: center; font-size: 16px;">{chart_title}</div>
                        <div style="display: flex; gap: 20px;">
                            <div style="flex: 1;">
                                {pie_svg}
                            </div>
                            <div style="flex: 1;">
                                {slices_html}
                            </div>
                        </div>
                    </div>
                    """
                elif chart_data:
                    max_value = (
                        max([d.get(y_field) or 0 for d in chart_data])
                        if y_field
                        else 100
                    )
                    bars_html = ""
                    for item in chart_data[:CHART_ROW_LIMIT]:
                        label = item.get(x_field, "N/A") if x_field else "N/A"
                        value = item.get(y_field) or 0 if y_field else 0
                        bar_width = (value / max_value * 100) if max_value > 0 else 0
                        bars_html += f"""
                        <div style="display: flex; align-items: center; margin-bottom: 5px;">
                            <div style="width: 100px; text-align: right; padding-right: 10px; font-size: 11px; color: #666;">{html.escape(str(label), quote=True)}</div>
                            <div style="flex: 1; background: #e9ecef; height: 20px; border-radius: 3px;">
                                <div style="width: {bar_width}%; height: 100%; background: #4CAF50; border-radius: 3px; min-width: 2px;"></div>
                            </div>
                            <div style="width: 80px; padding-left: 10px; font-size: 11px; color: #333;">{value:,.2f}</div>
                        </div>
                        """
                    chart_html = f"""
                    {label_html}
                    <div class="report-element chart-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 10px; text-align: center;">{chart_title}</div>
                        <div style="margin-top: 10px;">
                            {bars_html}
                        </div>
                    </div>
                    """
                else:
                    chart_html = f"""
                    {label_html}
                    <div class="report-element chart-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 10px; text-align: center;">{chart_title}</div>
                        <div style="color: #666; font-size: 11px; margin-bottom: 10px; text-align: center;">
                            Chart Type: {chart_type.capitalize()} | X-Axis: {x_field or "N/A"} | Y-Axis: {y_field or "N/A"}
                        </div>
                        <div style="margin-top: 10px; padding: 30px; background: #f8f9fa; border: 1px solid #ddd; text-align: center; color: #999;">
                            Chart requires data source connection to execute query
                        </div>
                    </div>
                    """
                elements_html += chart_html
            elif elem_type == "crosstab":
                row_field = props.get("rowField", "")
                col_field = props.get("columnField", "")
                value_field = props.get("valueField", "")
                aggregation = props.get("aggregation", "sum")
                elements_html += f"""
                {label_html}
                <div class="report-element crosstab-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                    <div style="font-weight: bold; margin-bottom: 10px;">Crosstab Pivot Table</div>
                    <div style="color: #666; font-size: 11px; margin-bottom: 10px;">
                        Rows: {row_field or "N/A"} | Columns: {col_field or "N/A"} | Value: {value_field or "N/A"} ({aggregation})
                    </div>
                    <div style="margin-top: 10px; padding: 20px; background: #f8f9fa; border: 1px solid #ddd; text-align: center; color: #999;">
                        Crosstab visualization requires data processing engine
                    </div>
                </div>
                """
            elif elem_type == "subreport":
                report_id = props.get("reportId", "")
                render_mode = props.get("render_mode", "inline")
                pass_parameters = props.get("pass_parameters", {}) or {}
                param_lines = (
                    "".join(
                        f'<div style="font-size: 11px; color: #555;">• {k}: <code>{v or "-"}</code></div>'
                        for k, v in pass_parameters.items()
                    )
                    or '<div style="font-size: 11px; color: #999;">No parameters</div>'
                )
                elements_html += f"""
                {label_html}
                <div class="report-element subreport-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                    <div style="font-weight: bold; margin-bottom: 5px;">Sub-report</div>
                    <div style="color: #666; font-size: 12px;">Report ID: {report_id or "Not set"} &nbsp;|&nbsp; Render mode: {render_mode}</div>
                    <div style="margin-top: 8px; padding: 8px; background: #f8f9fa; border: 1px solid #ddd; text-align: left;">
                        <div style="font-size: 11px; font-weight: bold; color: #777; margin-bottom: 3px;">Pass parameters:</div>
                        {param_lines}
                    </div>
                    <div style="margin-top: 10px; padding: 15px; background: #fffaf0; border: 1px dashed #eee; text-align: center; color: #999; font-size: 11px;">
                        Sub-report embedding requires report ID resolution
                    </div>
                </div>
                """
            else:
                elements_html += f"""
                {label_html}
                <div class="report-element unknown-element" style="padding: 5px; border: 1px dashed #ccc; margin: 5px 0; color: #999;">
                    {elem_type.capitalize()} Element (not rendered in preview)
                </div>
                """

        hide_name = section.get("hide_name", False)
        section_html = _build_section_html(
            section_type, section_name, elements_html, hide_name
        )
        sections_html += section_html

    return _build_page_html(title, description, sections_html, definition_json)


@router.websocket("/ws/{report_id}")
async def preview_websocket(
    websocket,
    report_id: str,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        pass
