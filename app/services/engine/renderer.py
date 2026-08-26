from __future__ import annotations

from typing import Any

import pandas as pd


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
            },
        }

        for section_def in definition.get("layout", {}).get("sections", []):
            rendered_section = self._render_section(section_def, data)
            result["sections"].append(rendered_section)

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

        if element_type == "text":
            return self._render_text(element_def)
        elif element_type == "table":
            return self._render_table(element_def, data)
        elif element_type == "chart":
            return self._render_chart(element_def, data)
        elif element_type == "crosstab":
            return self._render_crosstab(element_def, data)
        elif element_type == "image":
            return self._render_image(element_def)
        elif element_type == "subreport":
            return self._render_subreport(element_def, data)
        else:
            return {"type": element_type, "error": f"Unknown element type: {element_type}"}

    def _render_text(self, element_def: dict[str, Any]) -> dict[str, Any]:
        """Render a text element with variable substitution."""
        content = element_def.get("content", "")
        style = element_def.get("style", "normal")

        return {
            "type": "text",
            "content": content,
            "style": style,
        }

    def _render_table(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Render a table element with data."""
        data_source_id = element_def.get("data_source")
        df = data.get(data_source_id, pd.DataFrame())

        columns = element_def.get("columns", [])
        if columns and not df.empty:
            available_cols = [c["field"] for c in columns if c.get("field") in df.columns]
            df = df[available_cols]

        sort_field = element_def.get("sort")
        if sort_field and not df.empty:
            sort_parts = sort_field.split()
            field = sort_parts[0]
            ascending = "ASC" in sort_parts
            if field in df.columns:
                df = df.sort_values(by=field, ascending=ascending)

        limit = element_def.get("limit")
        if limit and not df.empty:
            df = df.head(limit)

        return {
            "type": "table",
            "columns": columns,
            "data": df.to_dict(orient="records"),
            "total_rows": len(df),
        }

    def _render_chart(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Render a chart element (returns configuration for client-side rendering)."""
        data_source_id = element_def.get("data_source")
        df = data.get(data_source_id, pd.DataFrame())

        return {
            "type": "chart",
            "chart_type": element_def.get("chart_type", "bar"),
            "x_field": element_def.get("x_field"),
            "y_field": element_def.get("y_field"),
            "width": element_def.get("width", "100%"),
            "height": element_def.get("height", "200px"),
            "data_source": data_source_id,
        }

    def _render_crosstab(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Render a cross-tab/pivot table element."""
        data_source_id = element_def.get("data_source")
        df = data.get(data_source_id, pd.DataFrame())

        if df.empty:
            return {"type": "crosstab", "data": []}

        rows = element_def.get("rows", [])
        columns = element_def.get("columns", [])
        value_field = element_def.get("value")
        aggregate = element_def.get("aggregate", "sum")

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
        except Exception as e:
            data = []

        return {
            "type": "crosstab",
            "data": data,
            "rows": rows,
            "columns": columns,
            "value_field": value_field,
            "aggregate": aggregate,
        }

    def _render_image(self, element_def: dict[str, Any]) -> dict[str, Any]:
        """Render an image element."""
        return {
            "type": "image",
            "source": element_def.get("source"),
            "position": element_def.get("position", "left"),
            "width": element_def.get("width"),
            "height": element_def.get("height"),
        }

    def _render_subreport(
        self, element_def: dict[str, Any], data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Render a subreport element."""
        return {
            "type": "subreport",
            "data_source": element_def.get("data_source"),
            "render_mode": element_def.get("render_mode", "inline"),
            "pass_parameters": element_def.get("pass_parameters", {}),
            "layout": element_def.get("layout", {}),
        }
