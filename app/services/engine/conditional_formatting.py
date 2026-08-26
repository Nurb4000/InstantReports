from __future__ import annotations

import re
from typing import Any

import pandas as pd


class ConditionalFormatter:
    """Apply conditional formatting rules to table data."""

    def apply_rules(
        self,
        data: list[dict[str, Any]],
        rules: list[dict[str, Any]],
        df: pd.DataFrame | None = None,
    ) -> list[dict[str, Any]]:
        """Apply conditional formatting rules to data rows.

        Args:
            data: List of row dictionaries
            rules: List of conditional format rules
            df: Optional DataFrame for column-based operations

        Returns:
            Data with formatting applied (each row gets 'formatting' key)
        """
        if not rules or not data:
            return data

        formatted_data = []
        for row_idx, row in enumerate(data):
            formatted_row = row.copy()
            cell_formats = {}
            row_format = None

            for rule in rules:
                result = self._evaluate_rule(rule, row, df, row_idx)
                if result:
                    if rule.get("target") == "row":
                        row_format = result
                    elif rule.get("target") == "cell":
                        field = rule.get("field")
                        if field and field in row:
                            cell_formats[field] = result

            formatted_row["formatting"] = {
                "row": row_format,
                "cells": cell_formats,
            }
            formatted_data.append(formatted_row)

        return formatted_data

    def _evaluate_rule(
        self,
        rule: dict[str, Any],
        row: dict[str, Any],
        df: pd.DataFrame | None = None,
        row_idx: int = 0,
    ) -> dict[str, Any] | None:
        """Evaluate a single conditional formatting rule.

        Returns:
            Format dict if rule matches, None otherwise
        """
        condition = rule.get("condition")
        if not condition:
            return None

        field = condition.get("field")
        operator = condition.get("operator", "==")
        value = condition.get("value")

        if field not in row:
            return None

        cell_value = row[field]

        try:
            matches = self._check_condition(cell_value, operator, value)
            if matches:
                return rule.get("format", {})
        except (TypeError, ValueError):
            pass

        return None

    def _check_condition(
        self, cell_value: Any, operator: str, condition_value: Any
    ) -> bool:
        """Check if a value matches a condition."""
        try:
            if operator == "==":
                return cell_value == condition_value
            elif operator == "!=":
                return cell_value != condition_value
            elif operator == ">":
                return float(cell_value) > float(condition_value)
            elif operator == ">=":
                return float(cell_value) >= float(condition_value)
            elif operator == "<":
                return float(cell_value) < float(condition_value)
            elif operator == "<=":
                return float(cell_value) <= float(condition_value)
            elif operator == "contains":
                return str(condition_value).lower() in str(cell_value).lower()
            elif operator == "not_contains":
                return str(condition_value).lower() not in str(cell_value).lower()
            elif operator == "starts_with":
                return str(cell_value).startswith(str(condition_value))
            elif operator == "ends_with":
                return str(cell_value).endswith(str(condition_value))
            elif operator == "between":
                low, high = condition_value
                return float(low) <= float(cell_value) <= float(high)
            elif operator == "not_between":
                low, high = condition_value
                return not (float(low) <= float(cell_value) <= float(high))
            elif operator == "is_empty":
                return cell_value is None or cell_value == ""
            elif operator == "is_not_empty":
                return cell_value is not None and cell_value != ""
        except (TypeError, ValueError):
            return False

        return False

    def get_css_styles(self, formatting: dict[str, Any]) -> str:
        """Convert formatting dict to CSS style string."""
        styles = []

        row_format = formatting.get("row", {})
        if row_format:
            if row_format.get("background"):
                styles.append(f"background-color: {row_format['background']};")
            if row_format.get("color"):
                styles.append(f"color: {row_format['color']};")
            if row_format.get("bold"):
                styles.append("font-weight: bold;")

        return " ".join(styles)
