from __future__ import annotations

import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.report import Report
from app.models.user import User
from app.routes.auth import get_current_user_optional

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/preview/{report_id}")
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
    from app.routes.admin import get_role_value
    role = get_role_value(current_user)
    if role not in ("admin", "designer") and report.created_by != current_user.id:
        # Non-designers can only preview their own reports
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        
        # If use_current is true, we'll need to get the current canvas state from the request
        # For now, just use the saved definition
        definition = report.definition
        title = report.name
        description = report.description or ""
        
        html_content = await render_report_with_data(
            definition, 
            title, 
            description,
            db=db
        )
        
        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {e!s}")


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
    
    try:
        import json
        definition = json.loads(definition_json)
        
        # Extract title and description from definition or use defaults
        title = definition.get("name", "Temporary Preview")
        description = definition.get("description", "")
        
        html_content = await render_report_with_data(
            definition,
            title,
            description,
            db=db
        )
        
        return HTMLResponse(content=html_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid definition: {e!s}")


async def render_report_with_data(definition: dict, title: str, description: str = "", db=None) -> str:
    """Render a report definition to HTML with actual data from database."""
    
    
    from app.services.engine.data_processor import DataProcessor
    
    # Get the first data source connection for query execution
    data_sources = definition.get("data_sources", [])
    connection_config = None
    
    logger.info(f"Data sources in definition: {len(data_sources)}")
    
    # Try to get connection from report definition
    if data_sources:
        conn_id = data_sources[0].get("connection_id")
        logger.info(f"Looking up connection ID: {conn_id}")
        if conn_id and db:
            from sqlalchemy import select

            from app.models.connection import DataConnection
            result = await db.execute(select(DataConnection).where(DataConnection.id == conn_id))
            connection = result.scalar_one_or_none()
            if connection:
                connection_config = connection.config
                logger.info(f"Found connection: {connection.name}")
            else:
                logger.warning(f"Connection not found for ID: {conn_id}")
    
    # If no connection in definition, try to find any PostgreSQL connection
    if not connection_config and db:
        from sqlalchemy import select

        from app.models.connection import DataConnection
        result = await db.execute(select(DataConnection).where(DataConnection.connector_type == 'postgresql').limit(1))
        connection = result.scalar_one_or_none()
        if connection:
            connection_config = connection.config
            logger.info(f"Using fallback connection: {connection.name}")
    
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
            
            # Build label HTML if custom label exists and not hidden
            label_html = ""
            if elem_label and not hide_label:
                label_html = f'''
                <div class="element-label" style="font-weight: bold; margin-bottom: 5px; color: #333; font-size: 14px;">
                    {elem_label}
                </div>
                '''
            
            if elem_type == "text":
                content = props.get("content", "")
                font_size = props.get("fontSize", 12)
                bold = props.get("bold", False)
                color = props.get("color", "#000000")
                elements_html += f'''
                {label_html}
                <div class="report-element text-element" style="font-size: {font_size}px; font-weight: {'bold' if bold else 'normal'}; color: {color}; padding: 5px; border: 1px dashed #ccc; margin: 5px 0;">
                    {content}
                </div>
                '''
            elif elem_type == "table":
                query = props.get("query", "")
                
                # Try to execute the query using the data source connector
                if query and connection_config:
                    try:
                        connector_type = connection_config.get('connector_type', 'postgresql')
                        
                        # Import and use the appropriate connector
                        from app.services.connectors.base import get_connector
                        connector = get_connector(connector_type)
                        
                        logger.info(f"Executing query using {connector_type} connector")
                        logger.info(f"Query: {query[:100]}...")
                        
                        # Execute query using the connector
                        df = await connector.execute_query(connection_config, query)
                        
                        # Commit after query to avoid transaction issues
                        await db.commit()

                        if df is not None and len(df) > 0:
                            # Apply report-level calculated fields (and grouping) before rendering
                            try:
                                df = DataProcessor().process(df, definition)
                            except Exception as processing_error:
                                logger.warning(f"Calculated field processing failed: {processing_error}")

                            # Convert DataFrame to HTML table
                            columns = list(df.columns)
                            rows = df.head(50).to_dict('records')  # Limit to 50 rows

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
                                    formatted_rows = cf.apply_rules(rows, formatting_rules, df)
                                except Exception as formatting_error:
                                    logger.warning(f"Conditional formatting failed: {formatting_error}")
                                    formatted_rows = rows

                            # Build table HTML
                            th_cells = ''.join('<th style="border: 1px solid #ddd; padding: 8px; text-align: left;">' + str(col) + '</th>' for col in columns)
                            td_rows = ''
                            for row in formatted_rows:
                                fmt = row.get("formatting") or {}
                                row_css = cf.get_css_styles(fmt) if cf else ''
                                tr_style = f' style="{row_css}"' if row_css else ''
                                td_cells = ''
                                for col in columns:
                                    cell_fmt = fmt.get("cells", {}).get(col)
                                    cell_style = ''
                                    if cf is not None and cell_fmt:
                                        cell_css = cf.get_css_styles({"row": None, "cells": {col: cell_fmt}})
                                        if cell_css:
                                            cell_style = f' style="{cell_css}"'
                                    td_cells += '<td style="border: 1px solid #ddd; padding: 6px;"' + cell_style + str(row.get(col, '')) + '</td>'
                                td_rows += '<tr' + tr_style + '>' + td_cells + '</tr>\n'
                            
                            table_html = f'''
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
                            '''
                            elements_html += table_html
                        else:
                            elements_html += '<div style="padding: 10px; color: #999;">No data returned</div>'
                    except Exception as e:
                        logger.error(f"Query execution failed: {e}")
                        elements_html += f'''
                        <div class="report-element table-element" style="padding: 5px; border: 1px dashed #ccc; margin: 5px 0; color: #dc3545;">
                            <div style="font-weight: bold;">Query Error</div>
                            <div style="font-size: 11px;">{str(e)[:200]}</div>
                        </div>
                        '''
                else:
                    # No database connection, show query as text
                    elements_html += f'''
                    {label_html}
                    <div class="report-element table-element" style="padding: 5px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 5px;">Table Element (Preview Mode)</div>
                        <pre style="font-size: 11px; background: #f8f9fa; padding: 5px; overflow: auto; max-height: 200px;">{query}</pre>
                    </div>
                    '''
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
                if connection_config:
                    try:
                        import logging
                        logger_chart = logging.getLogger(__name__)
                        
                        connector_type = connection_config.get('connector_type', 'postgresql')
                        from app.services.connectors.base import get_connector
                        connector = get_connector(connector_type)
                        
                        query = props.get("query", "")
                        if query:
                            logger_chart.info(f"Executing chart query using connector: {query[:100]}...")
                            df = await connector.execute_query(connection_config, query)
                            
                            if df is not None and len(df) > 0:
                                # Convert DataFrame to list of dicts for chart rendering
                                chart_data = df.head(10).to_dict(orient='records')
                                logger_chart.info(f"Chart query returned {len(chart_data)} rows")
                    except Exception as e:
                        logger.error(f"Failed to execute chart query via connector: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                
                # Render chart based on type
                if chart_type == "pie" and chart_data:
                    # Generate pie chart HTML
                    total = sum([d.get(y_field, 0) for d in chart_data])
                    colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
                    
                    slices_html = ""
                    for i, item in enumerate(chart_data):
                        label = item.get(x_field, "N/A") if x_field else "N/A"
                        value = item.get(y_field, 0) if y_field else 0
                        percentage = (value / total * 100) if total > 0 else 0
                        color = colors[i % len(colors)]
                        
                        slices_html += f'''
                        <div style="display: flex; align-items: center; margin-bottom: 8px;">
                            <div style="width: 20px; height: 20px; background: {color}; border-radius: 3px; margin-right: 10px;"></div>
                            <div style="flex: 1;">
                                <div style="font-size: 12px; font-weight: 500;">{label}</div>
                                <div style="font-size: 11px; color: #666;">${value:,.2f} ({percentage:.1f}%)</div>
                            </div>
                        </div>
                        '''
                    
                    chart_html = f'''
                    {label_html}
                    <div class="report-element chart-element" style="padding: 15px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 15px; text-align: center; font-size: 16px;">{chart_title}</div>
                        <div style="display: flex; gap: 20px;">
                            <div style="flex: 1;">
                                <div style="background: #f8f9fa; border-radius: 8px; padding: 20px; text-align: center; aspect-ratio: 1;">
                                    <div style="font-size: 48px; font-weight: bold; color: {colors[0] if chart_data else '#333'};">{len(chart_data)}</div>
                                    <div style="font-size: 12px; color: #666; margin-top: 5px;">Categories</div>
                                </div>
                            </div>
                            <div style="flex: 1;">
                                {slices_html}
                            </div>
                        </div>
                    </div>
                    '''
                elif chart_data:
                    # Bar chart (existing logic)
                    max_value = max([d.get(y_field, 0) for d in chart_data]) if y_field else 100
                    bars_html = ""
                    for item in chart_data[:10]:
                        label = item.get(x_field, "N/A") if x_field else "N/A"
                        value = item.get(y_field, 0) if y_field else 0
                        bar_width = (value / max_value * 100) if max_value > 0 else 0
                        bars_html += f'''
                        <div style="display: flex; align-items: center; margin-bottom: 5px;">
                            <div style="width: 100px; text-align: right; padding-right: 10px; font-size: 11px; color: #666;">{label}</div>
                            <div style="flex: 1; background: #e9ecef; height: 20px; border-radius: 3px;">
                                <div style="width: {bar_width}%; height: 100%; background: #4CAF50; border-radius: 3px; min-width: 2px;"></div>
                            </div>
                            <div style="width: 80px; padding-left: 10px; font-size: 11px; color: #333;">{value:,.2f}</div>
                        </div>
                        '''
                    chart_html = f'''
                    {label_html}
                    <div class="report-element chart-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 10px; text-align: center;">{chart_title}</div>
                        <div style="margin-top: 10px;">
                            {bars_html}
                        </div>
                    </div>
                    '''
                else:
                    chart_html = f'''
                    {label_html}
                    <div class="report-element chart-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 10px; text-align: center;">{chart_title}</div>
                        <div style="color: #666; font-size: 11px; margin-bottom: 10px; text-align: center;">
                            Chart Type: {chart_type.capitalize()} | X-Axis: {x_field or 'N/A'} | Y-Axis: {y_field or 'N/A'}
                        </div>
                        <div style="margin-top: 10px; padding: 30px; background: #f8f9fa; border: 1px solid #ddd; text-align: center; color: #999;">
                            Chart requires data source connection to execute query
                        </div>
                    </div>
                    '''
                elements_html += chart_html
                
                # Fallback to demo_data if no query results
                if not chart_data:
                    demo_data = element.get("demo_data", [])
                    if demo_data:
                        chart_data = demo_data
                
                if chart_data and len(chart_data) > 0:
                    max_value = max([d.get(y_field, 0) for d in chart_data]) if y_field else 100
                    bars_html = ""
                    for item in chart_data[:10]:  # Limit to 10 bars
                        label = item.get(x_field, "N/A") if x_field else "N/A"
                        value = item.get(y_field, 0) if y_field else 0
                        bar_width = (value / max_value * 100) if max_value > 0 else 0
                        bars_html += f'''
                        <div style="display: flex; align-items: center; margin-bottom: 5px;">
                            <div style="width: 100px; text-align: right; padding-right: 10px; font-size: 11px; color: #666;">{label}</div>
                            <div style="flex: 1; background: #e9ecef; height: 20px; border-radius: 3px;">
                                <div style="width: {bar_width}%; height: 100%; background: #4CAF50; border-radius: 3px; min-width: 2px;"></div>
                            </div>
                            <div style="width: 80px; padding-left: 10px; font-size: 11px; color: #333;">{value:,.2f}</div>
                        </div>
                        '''
                    chart_html = f'''
                    {label_html}
                    <div class="report-element chart-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 10px; text-align: center;">{chart_title}</div>
                        <div style="margin-top: 10px;">
                            {bars_html}
                        </div>
                    </div>
                    '''
                else:
                    chart_html = f'''
                    {label_html}
                    <div class="report-element chart-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                        <div style="font-weight: bold; margin-bottom: 10px; text-align: center;">{chart_title}</div>
                        <div style="color: #666; font-size: 11px; margin-bottom: 10px; text-align: center;">
                            Chart Type: {chart_type.capitalize()} | X-Axis: {x_field or 'N/A'} | Y-Axis: {y_field or 'N/A'}
                        </div>
                        <div style="margin-top: 10px; padding: 30px; background: #f8f9fa; border: 1px solid #ddd; text-align: center; color: #999;">
                            Chart requires data source connection to execute query
                        </div>
                    </div>
                    '''
                elements_html += chart_html
            elif elem_type == "crosstab":
                row_field = props.get("rowField", "")
                col_field = props.get("columnField", "")
                value_field = props.get("valueField", "")
                aggregation = props.get("aggregation", "sum")
                elements_html += f'''
                {label_html}
                <div class="report-element crosstab-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                    <div style="font-weight: bold; margin-bottom: 10px;">Crosstab Pivot Table</div>
                    <div style="color: #666; font-size: 11px; margin-bottom: 10px;">
                        Rows: {row_field or 'N/A'} | Columns: {col_field or 'N/A'} | Value: {value_field or 'N/A'} ({aggregation})
                    </div>
                    <div style="margin-top: 10px; padding: 20px; background: #f8f9fa; border: 1px solid #ddd; text-align: center; color: #999;">
                        Crosstab visualization requires data processing engine
                    </div>
                </div>
                '''
            elif elem_type == "subreport":
                report_id = props.get("reportId", "")
                render_mode = props.get("render_mode", "inline")
                pass_parameters = props.get("pass_parameters", {}) or {}
                param_lines = ''.join(
                    f'<div style="font-size: 11px; color: #555;">• {k}: <code>{v or '-'}</code></div>'
                    for k, v in pass_parameters.items()
                ) or '<div style="font-size: 11px; color: #999;">No parameters</div>'
                elements_html += f'''
                {label_html}
                <div class="report-element subreport-element" style="padding: 10px; border: 1px dashed #ccc; margin: 5px 0;">
                    <div style="font-weight: bold; margin-bottom: 5px;">Sub-report</div>
                    <div style="color: #666; font-size: 12px;">Report ID: {report_id or 'Not set'} &nbsp;|&nbsp; Render mode: {render_mode}</div>
                    <div style="margin-top: 8px; padding: 8px; background: #f8f9fa; border: 1px solid #ddd; text-align: left;">
                        <div style="font-size: 11px; font-weight: bold; color: #777; margin-bottom: 3px;">Pass parameters:</div>
                        {param_lines}
                    </div>
                    <div style="margin-top: 10px; padding: 15px; background: #fffaf0; border: 1px dashed #eee; text-align: center; color: #999; font-size: 11px;">
                        Sub-report embedding requires report ID resolution
                    </div>
                </div>
                '''
            else:
                elements_html += f'''
                {label_html}
                <div class="report-element unknown-element" style="padding: 5px; border: 1px dashed #ccc; margin: 5px 0; color: #999;">
                    {elem_type.capitalize()} Element (not rendered in preview)
                </div>
                '''
        
        hide_name = section.get("hide_name", False)
        header_html = ""
        if not hide_name:
            header_html = f'''            <div class="section-header" style="background: #f8f9fa; padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">
                {section_name}
            </div>
        '''
        section_html = f'''
        <div class="report-section section-{section_type.lower()}" style="page-break-inside: avoid; margin-bottom: 20px; border: 1px solid #ddd; padding: 10px;">
            {header_html}
            <div class="section-body" style="padding: 10px;">
                {elements_html if elements_html else '<div style="color: #999; font-style: italic;">No elements in this section</div>'}
            </div>
        </div>
        '''
        sections_html += section_html
    
    # Build HTML
    html = '''<!DOCTYPE html>
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
        .report-header {
            border-bottom: 2px solid #333;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }
        .report-title {
            font-size: 24px;
            font-weight: bold;
            margin: 0;
        }
        .report-description {
            color: #666;
            margin-top: 5px;
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
        }
    </style>
</head>
<body>
    <div class="report-container">
        <div class="report-header">
            <h1 class="report-title">REPORT_TITLE</h1>
            REPORT_DESCRIPTION
        </div>
        
        <div class="report-body">
            SECTIONS_HTML
        </div>
        
        <div class="report-footer" style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd; text-align: center; color: #999; font-size: 12px;">
            Preview Mode - Showing sample data
        </div>
    </div>
</body>
</html>'''
    
    # Replace placeholders
    html = html.replace("REPORT_TITLE", title)
    if description:
        html = html.replace("REPORT_DESCRIPTION", f'<p class="report-description">{description}</p>')
    else:
        html = html.replace("REPORT_DESCRIPTION", "")
    html = html.replace("SECTIONS_HTML", sections_html)
    
    return html


@router.websocket("/ws/{report_id}")
async def preview_websocket(
    websocket,
    report_id: str,
    current_user: User | None = Depends(get_current_user_optional),
):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        pass
