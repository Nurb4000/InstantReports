from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.engine.conditional_formatting import ConditionalFormatter


class ReportRenderer:
    """Core report renderer that processes definitions into output."""

    def render(self, definition: dict[str, Any], data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Render a report definition with provided data.

        Args:
            definition: The report definition JSON
            data: Dictionary of data source ID -> DataFrame

        Returns:
            Rendered report structure with sections, elements, and metadata
        """
        result = {
            "name": definition.get("name", "Report"),
            "description": definition.get("description", ""),
            "sections": [],
            "metadata": {
                "page": definition.get("layout", {}).get("page", {}),
                "parameters": definition.get("parameters", []),
                "page_number": 1,
                "total_pages": 1,
            },
        }

        for section_def in definition.get("layout", {}).get("sections", []):
            rendered_section = self._render_section(section_def, data)
            result["sections"].append(rendered_section)

        return result

    def resolve_tokens(self, text: str, context: dict[str, Any]) -> str:
        """Resolve special tokens in text (page numbers, dates, etc.).

        Supported tokens:
        - {{page.number}} - Current page number
        - {{page.total}} - Total pages
        - {{date.now}} - Current date/time
        - {{report.name}} - Report name
        - {{user.name}} - Current user name
        """
        import datetime

        replacements = {
            "{{page.number}}": str(context.get("page_number", 1)),
            "{{page.total}}": str(context.get("total_pages", 1)),
            "{{date.now}}": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "{{date.today}}": datetime.datetime.now(datetime.timezone.utc).date().strftime("%Y-%m-%d"),
            "{{report.name}}": context.get("report_name", ""),
            "{{user.name}}": context.get("user_name", ""),
        }

        result = text
        for token, value in replacements.items():
            result = result.replace(token, str(value))

        return result

    def _render_section(
        self, section_def: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Render a single section (header, detail, summary, footer)."""
        rendered = {
            "type": section_def.get("type", "detail"),
            "elements": [],
        }

        if section_def.get("group_by"):
            rendered["group_by"] = section_def["group_by"]

        for element_def in section_def.get("elements", []):
            rendered_element = self._render_element(element_def, data)
            rendered["elements"].append(rendered_element)

        return rendered

    def _render_element(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Render a single element (text, table, chart, etc.)."""
        element_type = element_def.get("type", "text")
        element_label = element_def.get("label", "")

        if element_type == "text":
            return self._render_text(element_def)
        elif element_type == "table":
            return self._render_table(element_def, data, element_label)
        elif element_type == "chart":
            return self._render_chart(element_def, data, element_label)
        elif element_type == "crosstab":
            return self._render_crosstab(element_def, data, element_label)
        elif element_type == "image":
            return self._render_image(element_def, element_label=element_label)
        elif element_type == "subreport":
            return self._render_subreport(element_def, data, element_label)
        else:
            return {"type": element_type, "error": f"Unknown element type: {element_type}"}

    def _render_text(self, element_def: dict[str, Any]) -> dict[str, Any]:
        """Render a text element with variable substitution."""
        content = element_def.get("content", "")
        style = element_def.get("style", "normal")
        label = element_def.get("label", "")

        return {
            "type": "text",
            "content": content,
            "style": style,
            "label": label,
        }

    def _render_table(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame], element_label: str = ""
    ) -> dict[str, Any]:
        """Render a table element with data."""
        config = element_def.get("properties") or {}
        data_source_id = element_def.get("data_source")
        df = data.get(data_source_id, pd.DataFrame())

        columns = config.get("columns") or element_def.get("columns", [])
        if not columns and not df.empty:
            # Query-only tables may ship without explicit column definitions.
            # Exporters (PDF/Excel/CSV/HTML) require at least one column, so
            # derive them from the DataFrame here; keep the original key type
            # so exporter row lookups match to_dict(orient="records") output.
            columns = [{"field": c, "header": str(c)} for c in df.columns]
        if columns and not df.empty:
            available_cols = [c["field"] for c in columns if c.get("field") in df.columns]
            df = df[available_cols]

        sort_field = config.get("sort") or element_def.get("sort")
        if sort_field and not df.empty:
            sort_parts = sort_field.split()
            field = sort_parts[0]
            ascending = "ASC" in sort_parts
            if field in df.columns:
                df = df.sort_values(by=field, ascending=ascending)

        limit = config.get("limit") or element_def.get("limit")
        if limit and not df.empty:
            df = df.head(limit)

        records = df.to_dict(orient="records")
        formatting_rules = element_def.get("formatting_rules") or (element_def.get("properties") or {}).get("formatting_rules")
        if formatting_rules:
            records = ConditionalFormatter().apply_rules(records, formatting_rules)

        return {
            "type": "table",
            "columns": columns,
            "data": records,
            "total_rows": len(records),
            "label": element_label,
        }

    def _render_chart(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame], element_label: str = ""
    ) -> dict[str, Any]:
        """Render a chart element (returns configuration for client-side rendering)."""
        config = element_def.get("properties") or {}
        data_source_id = element_def.get("data_source")

        rendered = {
            "type": "chart",
            "chart_type": config.get("type") or element_def.get("chart_type", "bar"),
            "x_field": config.get("xField") or element_def.get("x_field"),
            "y_field": config.get("yField") or element_def.get("y_field"),
            "title": config.get("title") or element_def.get("title"),
            "width": config.get("width") or element_def.get("width", "100%"),
            "height": config.get("height") or element_def.get("height", "200px"),
            "data_source": data_source_id,
            "label": element_label,
        }

        # Attach the underlying DataFrame when available so server-side
        # exporters (e.g. PDF) can plot the chart instead of an empty series.
        if data_source_id and data_source_id in data:
            rendered["data"] = data[data_source_id]

        return rendered

    def _render_crosstab(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame], element_label: str = ""
    ) -> dict[str, Any]:
        """Render a cross-tab/pivot table element."""
        config = element_def.get("properties") or {}
        data_source_id = element_def.get("data_source")
        df = data.get(data_source_id, pd.DataFrame())

        if df.empty:
            return {"type": "crosstab", "data": []}

        # The designer stores crosstab config under rowField/columnField/
        # valueField/aggregation; accept those primarily and fall back to the
        # older rows/columns/value/aggregate keys for saved definitions.
        rows = config.get("rowField") or element_def.get("rows") or []
        columns = config.get("columnField") or element_def.get("columns") or []
        value_field = config.get("valueField") or element_def.get("value")
        aggregate = (
            config.get("aggregation")
            or element_def.get("aggregate")
            or "sum"
        )

        if not rows or not columns or not value_field:
            return {"type": "crosstab", "error": "Missing required fields"}

        try:
            pivot_df = pd.pivot_table(
                df,
                values=value_field,
                index=rows if isinstance(rows, list) else [rows],
                columns=columns if isinstance(columns, list) else [columns],
                aggfunc=aggregate,
                margins=True,
                margins_name="Total",
            )
            data = pivot_df.reset_index().to_dict(orient="records")
        except Exception:
            data = []

        return {
            "type": "crosstab",
            "data": data,
            "rows": rows,
            "columns": columns,
            "value_field": value_field,
            "aggregate": aggregate,
            "label": element_label,
        }

    def _render_image(self, element_def: dict[str, Any], element_label: str = "") -> dict[str, Any]:
        """Render an image element."""
        config = element_def.get("properties") or {}
        return {
            "type": "image",
            "source": config.get("src") or element_def.get("src") or element_def.get("source"),
            "position": config.get("position") or element_def.get("position", "left"),
            "width": config.get("width") or element_def.get("width"),
            "height": config.get("height") or element_def.get("height"),
            "label": element_label,
        }

    def _render_subreport(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame], element_label: str = ""
    ) -> dict[str, Any]:
        """Render a subreport element."""
        properties = element_def.get("properties") or {}
        return {
            "type": "subreport",
            "data_source": element_def.get("data_source") or properties.get("data_source"),
            "label": element_label,
            "render_mode": element_def.get("render_mode") or properties.get("render_mode") or "inline",
            "pass_parameters": element_def.get("pass_parameters") or properties.get("pass_parameters") or {},
            "layout": element_def.get("layout") or properties.get("layout") or {},
        }
