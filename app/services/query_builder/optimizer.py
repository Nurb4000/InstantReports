"""Query optimization service for the SQL Query Builder.

Analyzes a :class:`QueryConfig` and (optionally) a fetched
:class:`SchemaResponse` to produce concrete, actionable performance suggestions
such as missing indexes on JOIN/WHERE columns and ``SELECT *`` usage.
"""

from __future__ import annotations

from typing import Any

from app.services.query_builder.config import (
    QueryConfig,
    SchemaResponse,
)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _build_indexed_columns(schema: SchemaResponse) -> dict[str, set[str]]:
    """Map table name -> set of columns that already have a PK/FK index."""
    indexed: dict[str, set[str]] = {}
    if not schema:
        return indexed
    for table in schema.tables:
        cols: set[str] = set()
        for col in table.columns:
            if col.is_primary_key or col.is_foreign_key:
                cols.add(col.name)
        indexed[table.name] = cols
    return indexed


def _split_field(field: str) -> tuple[str | None, str | None]:
    """Split a 'table.column' string into its parts."""
    if "." in field:
        table, column = field.rsplit(".", 1)
        return table, column
    return None, field


def analyze_query(
    config: QueryConfig,
    schema: SchemaResponse | None = None,
) -> list[dict[str, Any]]:
    """Return a list of optimization suggestions for the given query config."""
    suggestions: list[dict[str, Any]] = []
    indexed = _build_indexed_columns(schema)

    # 1. SELECT * (no explicit columns).
    if not config.select:
        suggestions.append({
            "severity": "low",
            "code": "select_star",
            "message": "SELECT * retrieves all columns. List only the columns you need to reduce I/O.",
        })

    # 2. Missing WHERE clause.
    if not config.where:
        suggestions.append({
            "severity": "medium",
            "code": "missing_where",
            "message": "No WHERE clause. Add filters to avoid scanning the full result set.",
        })

    # 3. GROUP BY without any aggregation.
    if config.group_by and not any(c.aggregation for c in config.select):
        suggestions.append({
            "severity": "low",
            "code": "group_by_no_agg",
            "message": "GROUP BY present but no aggregate functions. Confirm this is intended.",
        })

    # 4. Index suggestions for JOIN columns.
    for join in config.joins:
        for role, table, column in (
            ("left", join.on_left_table, join.on_left_column),
            ("right", join.on_right_table, join.on_right_column),
        ):
            if not table or not column:
                continue
            if table in indexed and column in indexed[table]:
                continue
            suggestions.append({
                "severity": "high" if join.join_type.value == "INNER" else "medium",
                "code": "missing_index",
                "message": f"JOIN {role} column '{table}.{column}' is not a primary/foreign key. Consider an index.",
                "table": table,
                "column": column,
            })

    # 5. Index suggestions for WHERE-filtered columns.
    for where in config.where:
        table, column = _split_field(where.field)
        if table and table in indexed and column in indexed[table]:
            continue
        if table:
            suggestions.append({
                "severity": "medium",
                "code": "missing_index",
                "message": f"WHERE column '{table}.{column}' is not indexed. Consider adding an index.",
                "table": table,
                "column": column,
            })

    suggestions.sort(key=lambda s: SEVERITY_ORDER.get(s["severity"], 3))
    return suggestions


# Convenience function
def optimize_query(config: QueryConfig, schema: SchemaResponse | None = None) -> list[dict[str, Any]]:
    """Analyze a query config and return optimization suggestions."""
    return analyze_query(config, schema)
