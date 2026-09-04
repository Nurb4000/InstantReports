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
        """Evaluate a calculated-field expression against DataFrame columns.

        Delegates to :class:`CalculatedFieldEvaluator` so the preview/scheduled
        export path stays in sync with the Fields-tab Test path -- most importantly
        both accept ``{{ column }}`` expressions with surrounding whitespace.
        """
        from app.services.engine.calculated_fields import CalculatedFieldEvaluator

        return CalculatedFieldEvaluator().evaluate(expression, df)

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
