"""SQL Generator service for converting query configurations to SQL."""

from __future__ import annotations

import logging
from typing import List, Optional

from app.services.query_builder.config import (
    Aggregation,
    JoinConfig,
    JoinType,
    OrderByField,
    Operator,
    QueryConfig,
    SelectColumn,
    WhereFilter,
)

logger = logging.getLogger(__name__)


class SQLGenerator:
    """Generates SQL queries from QueryConfig objects."""

    @staticmethod
    def generate_select(columns: List[SelectColumn]) -> str:
        """Generate SELECT clause."""
        if not columns:
            return "*"

        parts = []
        for col in columns:
            if col.aggregation and col.aggregation != Aggregation.NONE:
                expr = f"{col.aggregation.value}({col.table}.{col.column})"
            else:
                expr = f"{col.table}.{col.column}"

            if col.alias:
                parts.append(f"{expr} AS {col.alias}")
            else:
                parts.append(expr)

        return ", ".join(parts)

    @staticmethod
    def generate_from(tables: List[str]) -> str:
        """Generate FROM clause."""
        if not tables:
            return ""
        return f"FROM {tables[0]}"

    @staticmethod
    def generate_joins(joins: List[JoinConfig]) -> str:
        """Generate JOIN clauses."""
        if not joins:
            return ""

        parts = []
        for join in joins:
            parts.append(join.to_sql())

        return "\n".join(parts)

    @staticmethod
    def generate_where(filters: List[WhereFilter]) -> str:
        """Generate WHERE clause."""
        if not filters:
            return ""

        parts = []
        for i, filter in enumerate(filters):
            if i == 0:
                parts.append(filter.to_sql())
            else:
                logic = filter.logic
                parts.append(f"{logic} {filter.to_sql()}")

        return "WHERE " + " ".join(parts)

    @staticmethod
    def generate_group_by(columns: List[str]) -> str:
        """Generate GROUP BY clause."""
        if not columns:
            return ""
        return f"GROUP BY {', '.join(columns)}"

    @staticmethod
    def generate_order_by(fields: List[OrderByField]) -> str:
        """Generate ORDER BY clause."""
        if not fields:
            return ""

        parts = []
        for field in fields:
            parts.append(field.to_sql())

        return f"ORDER BY {', '.join(parts)}"

    @classmethod
    def generate(cls, config: QueryConfig) -> str:
        """Generate complete SQL query from configuration."""
        parts = []

        # SELECT
        select_clause = cls.generate_select(config.select)
        parts.append(f"SELECT {select_clause}")

        # FROM
        from_clause = cls.generate_from(config.from_tables)
        if from_clause:
            parts.append(from_clause)

        # JOINs
        join_clause = cls.generate_joins(config.joins)
        if join_clause:
            parts.append(join_clause)

        # WHERE
        where_clause = cls.generate_where(config.where)
        if where_clause:
            parts.append(where_clause)

        # GROUP BY
        group_by_clause = cls.generate_group_by(config.group_by)
        if group_by_clause:
            parts.append(group_by_clause)

        # ORDER BY
        order_by_clause = cls.generate_order_by(config.order_by)
        if order_by_clause:
            parts.append(order_by_clause)

        return "\n".join(parts)

    @staticmethod
    def validate_config(config: QueryConfig) -> tuple[bool, List[str]]:
        """Validate query configuration and return (is_valid, errors)."""
        errors = []

        # Check SELECT columns
        if not config.select and not config.from_tables:
            errors.append("Must select at least one column or table")

        # Check FROM tables
        if not config.from_tables:
            errors.append("Must specify at least one table in FROM clause")

        # Validate JOIN conditions
        for join in config.joins:
            if join.on_left_table not in config.from_tables and not any(
                j.table == join.on_left_table for j in config.joins
            ):
                errors.append(f"JOIN left table '{join.on_left_table}' not in FROM clause")

            if join.on_right_table != join.table:
                errors.append(f"JOIN right table must match JOIN table '{join.table}'")

        # Validate WHERE filters
        for where in config.where:
            if "." not in where.field:
                errors.append(f"WHERE field '{where.field}' must be in 'table.column' format")

        # Validate GROUP BY columns
        for col in config.group_by:
            if "." not in col:
                errors.append(f"GROUP BY column '{col}' must be in 'table.column' format")

        # Validate ORDER BY fields
        for field in config.order_by:
            if "." not in field.field:
                errors.append(f"ORDER BY field '{field.field}' must be in 'table.column' format")

        is_valid = len(errors) == 0
        return is_valid, errors


# Convenience function
def generate_sql(config: QueryConfig) -> str:
    """Generate SQL from query configuration."""
    return SQLGenerator.generate(config)


def validate_query(config: QueryConfig) -> tuple[bool, List[str]]:
    """Validate query configuration."""
    return SQLGenerator.validate_config(config)
