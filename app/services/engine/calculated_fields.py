from __future__ import annotations

import re
from typing import Any

import pandas as pd


class CalculatedFieldEvaluator:
    """Evaluate calculated field expressions against DataFrame columns."""

    def evaluate(
        self,
        expression: str,
        df: pd.DataFrame,
        context: dict[str, Any] | None = None,
    ) -> pd.Series:
        """Evaluate a calculated field expression.

        Args:
            expression: Expression string (e.g., "{{revenue}} - {{cost}}")
            df: DataFrame with source columns
            context: Optional additional context variables

        Returns:
            Series with evaluated results
        """
        if not expression or df.empty:
            return pd.Series([None] * len(df), index=df.index)

        # Replace {{column}} references with actual column access
        processed_expr = self._process_expression(expression, df)

        # Add context variables
        eval_context = {"df": df, "__builtins__": {}}
        if context:
            eval_context.update(context)

        try:
            result = eval(processed_expr, eval_context)  # noqa: S307
            if isinstance(result, pd.Series):
                return result
            else:
                return pd.Series([result] * len(df), index=df.index)
        except Exception:
            return pd.Series([None] * len(df), index=df.index)

    def _process_expression(self, expression: str, df: pd.DataFrame) -> str:
        """Process {{column}} references in expression."""
        # Find all {{...}} patterns
        pattern = r"\{\{([^}]+)\}\}"
        matches = re.findall(pattern, expression)

        processed = expression
        for match in matches:
            col = match.strip()
            if col in df.columns:
                # Replace with pandas column access
                replacement = f"df['{col}']"
                processed = processed.replace(f"{{{{{match}}}}}", replacement)
            else:
                # Keep as-is (might be a constant or function call)
                pass

        return processed

    def validate_expression(self, expression: str) -> tuple[bool, str]:
        """Validate a calculated field expression.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not expression:
            return False, "Expression cannot be empty"

        # Check for balanced braces
        if expression.count("{") != expression.count("}"):
            return False, "Unbalanced braces"

        # Check for dangerous patterns
        dangerous_patterns = [
            r"\bimport\b",
            r"\bexec\b",
            r"\beval\b",
            r"\bos\.",
            r"\bsubprocess",
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, expression, re.IGNORECASE):
                return False, f"Expression contains forbidden pattern: {pattern}"

        return True, "Valid"
