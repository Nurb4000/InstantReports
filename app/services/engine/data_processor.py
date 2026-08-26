from __future__ import annotations

from typing import Any

import pandas as pd


class DataProcessor:
    """Process data for reports: calculated fields, grouping, aggregation."""

    def process(self, df: pd.DataFrame, definition: dict[str, Any]) -> pd.DataFrame:
        """Process a DataFrame according to report definition.

        Args:
            df: Input DataFrame
            definition: Report definition with calculated_fields, group_by, etc.

        Returns:
            Processed DataFrame
        """
        if df.empty:
            return df

        df = self._add_calculated_fields(df, definition)
        df = self._apply_grouping(df, definition)
        return df

    def _add_calculated_fields(
        self, df: pd.DataFrame, definition: dict[str, Any]
    ) -> pd.DataFrame:
        """Add calculated fields from report definition."""
        for field_def in definition.get("calculated_fields", []):
            name = field_def["name"]
            expression = field_def["expression"]

            try:
                df[name] = self._evaluate_expression(df, expression)
            except Exception:
                df[name] = None

        return df

    def _evaluate_expression(self, df: pd.DataFrame, expression: str) -> pd.Series:
        """Evaluate a simple expression against DataFrame columns."""
        import re

        column_refs = re.findall(r"\{\{(\w+)\}\}", expression)
        for col in column_refs:
            if col in df.columns:
                expression = expression.replace(f"{{{{{col}}}}}", f"df['{col}']")

        try:
            return eval(expression, {"df": df, "__builtins__": {}}, {})
        except Exception:
            return pd.Series([None] * len(df), index=df.index)

    def _apply_grouping(
        self, df: pd.DataFrame, definition: dict[str, Any]
    ) -> pd.DataFrame:
        """Apply grouping and aggregation if specified."""
        group_by = definition.get("group_by")
        if not group_by or group_by not in df.columns:
            return df

        aggregations = definition.get("aggregations", {})
        if aggregations:
            df = df.groupby(group_by, as_index=False).agg(aggregations)

        return df

    def filter_data(
        self, df: pd.DataFrame, filters: list[dict[str, Any]]
    ) -> pd.DataFrame:
        """Apply filters to a DataFrame."""
        for f in filters:
            field = f.get("field")
            operator = f.get("operator", "==")
            value = f.get("value")

            if field not in df.columns:
                continue

            if operator == "==":
                df = df[df[field] == value]
            elif operator == "!=":
                df = df[df[field] != value]
            elif operator == ">":
                df = df[df[field] > value]
            elif operator == ">=":
                df = df[df[field] >= value]
            elif operator == "<":
                df = df[df[field] < value]
            elif operator == "<=":
                df = df[df[field] <= value]
            elif operator == "contains":
                df = df[df[field].astype(str).str.contains(str(value), case=False, na=False)]

        return df
