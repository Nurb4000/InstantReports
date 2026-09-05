from __future__ import annotations

import ast
import operator
import re
from typing import Any

import pandas as pd

# Binary/unary/comparison operators permitted in a calculated-field expression.
# Everything else (attributes, calls to unlisted names, comprehensions, ...) is
# rejected so an expression can never reach object internals or builtins.
_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_SAFE_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_SAFE_COMPARES = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

# Pure, side-effect-free callables that are safe to expose to expressions.
_SAFE_CALLS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "len": len, "sum": sum, "str": str, "int": int, "float": float,
}

# The only name an expression may reference: the source DataFrame.
_SAFE_NAMES = {"df"}


class _SafeExpressionEvaluator:
    """Recursively evaluate a processed calculated-field expression.

    The expression is expected to reference columns as ``df['name']`` (produced
    by :meth:`CalculatedFieldEvaluator._process_expression`). Only a narrow set
    of AST node types is accepted; any attribute access, unlisted call, or other
    construct raises ``ValueError``, which callers treat as an evaluation error.
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def eval_node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.eval_node(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
            left = self.eval_node(node.left)
            right = self.eval_node(node.right)
            return _SAFE_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARYOPS:
            return _SAFE_UNARYOPS[type(node.op)](self.eval_node(node.operand))
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = self.eval_node(node.left)
            right = self.eval_node(node.right)
            op = node.ops[0]
            if type(op) in _SAFE_COMPARES:
                return _SAFE_COMPARES[type(op)](left, right)
            raise ValueError(f"Unsupported comparison: {type(op).__name__}")
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in _SAFE_NAMES:
                raise ValueError(f"Unknown name: {node.id}")
            return self.df
        if isinstance(node, ast.Subscript):
            value = self.eval_node(node.value)
            if value is not self.df or not isinstance(value, pd.DataFrame):
                raise ValueError("Only df[...] column lookups are allowed")
            slice_node = node.slice
            # Support both df['col'] and df[col] (a bare Name we resolve here).
            if isinstance(slice_node, ast.Name) and slice_node.id in _SAFE_NAMES:
                key = None
            else:
                key = self.eval_node(slice_node)
            if not isinstance(key, str) or key not in value.columns:
                raise ValueError(f"Unknown column: {key!r}")
            return value[key]
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in _SAFE_CALLS):
                raise ValueError("Only whitelisted functions may be called")
            if node.keywords or any(a is None for a in node.args):
                raise ValueError("Unsupported call arguments")
            args = [self.eval_node(a) for a in node.args]
            return _SAFE_CALLS[node.func.id](*args)
        raise ValueError(f"Disallowed expression element: {type(node).__name__}")


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

        try:
            tree = ast.parse(processed_expr, mode="eval")
            result = _SafeExpressionEvaluator(df).eval_node(tree)
        except Exception:
            return pd.Series([None] * len(df), index=df.index)

        if isinstance(result, pd.Series):
            return result
        return pd.Series([result] * len(df), index=df.index)

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
