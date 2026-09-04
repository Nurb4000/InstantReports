"""Query configuration models for the SQL Query Builder."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JoinType(str, Enum):
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL"


class Operator(str, Enum):
    EQUALS = "="
    NOT_EQUALS = "!="
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    LIKE = "LIKE"
    IN = "IN"
    BETWEEN = "BETWEEN"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"


class Aggregation(str, Enum):
    NONE = None
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class SelectColumn(BaseModel):
    """Represents a column selected in the SELECT clause."""

    table: str
    column: str
    alias: str | None = None
    aggregation: Aggregation | None = None

    def expression(self) -> str:
        """Return the raw SQL expression for this column (no alias)."""
        if self.aggregation and self.aggregation != Aggregation.NONE:
            return f"{self.aggregation.value}({self.table}.{self.column})"
        return f"{self.table}.{self.column}"

    def base_name(self) -> str:
        """Return the result-column name this column produces without an alias.

        A plain column yields its bare name while an aggregate gets a synthetic
        ``{FUNC}_{table}_{column}`` name so collisions are detected consistently.
        """
        if self.aggregation and self.aggregation != Aggregation.NONE:
            return f"{self.aggregation.value.lower()}_{self.table}.{self.column}"
        return self.column

    def to_sql(self) -> str:
        """Generate SQL for this select column."""
        col_expr = self.expression()
        if self.alias:
            return f"{col_expr} AS {self.alias}"
        return col_expr


def resolve_select_names(columns: list[SelectColumn]) -> list[str]:
    """Return a collision-free result-column name for each select column.

    Explicit aliases always win and are never rewritten. Otherwise the natural
    name (bare column, or synthetic aggregate name) is used. When two columns
    share a natural name -- the classic ``SELECT a.id, b.id`` join collision
    that would otherwise be silently dropped during result materialization --
    the later column is qualified as ``table__name`` and further duplicates get
    an incrementing suffix so every selected column survives with a unique key.
    """
    resolved: list[str] = []
    for col in columns:
        if col.alias:
            resolved.append(col.alias)
            continue
        base = col.base_name()
        if base not in resolved:
            resolved.append(base)
            continue
        candidate = f"{col.table}__{base}"
        suffix = 2
        while candidate in resolved:
            candidate = f"{col.table}__{base}_{suffix}"
            suffix += 1
        resolved.append(candidate)
    return resolved


class JoinConfig(BaseModel):
    """Represents a JOIN clause."""

    join_type: JoinType = JoinType.INNER
    table: str
    on_left_table: str
    on_left_column: str
    on_right_table: str
    on_right_column: str

    def to_sql(self) -> str:
        """Generate SQL for this JOIN."""
        return (
            f"{self.join_type.value} JOIN {self.table} "
            f"ON {self.on_left_table}.{self.on_left_column} = "
            f"{self.on_right_table}.{self.on_right_column}"
        )


class WhereFilter(BaseModel):
    """Represents a WHERE clause filter."""

    field: str  # table.column format
    operator: Operator
    value: Any
    logic: str = "AND"  # AND or OR

    def to_sql(self) -> str:
        """Generate SQL for this filter."""
        if self.operator == Operator.IS_NULL:
            return f"{self.field} IS NULL"
        if self.operator == Operator.IS_NOT_NULL:
            return f"{self.field} IS NOT NULL"

        if (
            self.operator == Operator.BETWEEN
            and isinstance(self.value, (list, tuple))
            and len(self.value) == 2
        ):
            return (
                f"{self.field} BETWEEN {self._quote(self.value[0])} "
                f"AND {self._quote(self.value[1])}"
            )

        if self.operator == Operator.LIKE:
            return f"{self.field} LIKE {self._quote(self.value)}"

        if isinstance(self.value, list):
            values_str = ", ".join(self._quote(v) for v in self.value)
            return f"{self.field} IN ({values_str})"

        return f"{self.field} {self.operator.value} {self._quote(self.value)}"

    @staticmethod
    def _quote(value: Any) -> str:
        """Wrap ``value`` in a SQL string literal, escaping embedded quotes.

        Single quotes are doubled per the SQL standard so values containing
        apostrophes (e.g. ``O'Brien``) don't break the query and untrusted
        input can't escape the literal (SQL injection).
        """
        return "'" + str(value).replace("'", "''") + "'"


class OrderByField(BaseModel):
    """Represents an ORDER BY field."""

    field: str
    direction: str = "ASC"  # ASC or DESC

    def to_sql(self) -> str:
        """Generate SQL for this order by field."""
        return f"{self.field} {self.direction}"


class QueryConfig(BaseModel):
    """Complete query configuration."""

    version: str = "1.0"
    select: list[SelectColumn] = Field(default_factory=list)
    from_tables: list[str] = Field(default_factory=list)
    joins: list[JoinConfig] = Field(default_factory=list)
    where: list[WhereFilter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderByField] = Field(default_factory=list)

    def to_sql(self) -> str:
        """Generate complete SQL query from configuration."""
        sql_parts = []

        # SELECT
        if self.select:
            resolved_names = resolve_select_names(self.select)
            select_cols = []
            for col, name in zip(self.select, resolved_names):
                expr = col.expression()
                # Only emit a resolver-added alias when the collision resolver
                # had to rename the column; otherwise keep the natural form
                # (explicit aliases are always emitted via ``expr AS``).
                if name == col.base_name():
                    select_cols.append(expr)
                else:
                    select_cols.append(f"{expr} AS {name}")
            sql_parts.append(f"SELECT {', '.join(select_cols)}")
        else:
            sql_parts.append("SELECT *")

        # FROM
        if self.from_tables:
            sql_parts.append(f"FROM {self.from_tables[0]}")

        # JOINs
        for join in self.joins:
            sql_parts.append(join.to_sql())

        # WHERE
        if self.where:
            where_clauses = [f.to_sql() for f in self.where]
            if len(where_clauses) == 1:
                sql_parts.append(f"WHERE {where_clauses[0]}")
            else:
                # Each filter's ``logic`` describes how it joins to the previous
                # filter; honour it per-filter rather than reusing the first one.
                joined = where_clauses[0]
                for i in range(1, len(where_clauses)):
                    op = self.where[i].logic
                    joined = f"{joined} {op} {where_clauses[i]}"
                sql_parts.append(f"WHERE {joined}")

        # GROUP BY
        if self.group_by:
            sql_parts.append(f"GROUP BY {', '.join(self.group_by)}")

        # ORDER BY
        if self.order_by:
            order_cols = [field.to_sql() for field in self.order_by]
            sql_parts.append(f"ORDER BY {', '.join(order_cols)}")

        return "\n".join(sql_parts)


class QueryTemplate(BaseModel):
    """Saved query template for reuse."""

    id: uuid.UUID | None = None
    name: str
    description: str | None = None
    query_config: QueryConfig
    connection_id: uuid.UUID
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        json_encoders = {
            uuid.UUID: lambda v: str(v),
            datetime: lambda v: v.isoformat(),
        }


class SchemaTable(BaseModel):
    """Represents a table in the schema browser."""

    name: str
    columns: list[SchemaColumn]


class SchemaColumn(BaseModel):
    """Represents a column in the schema browser."""

    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_key_table: str | None = None
    foreign_key_column: str | None = None


class SchemaResponse(BaseModel):
    """Response from schema browser endpoint."""

    tables: list[SchemaTable]
    connection_name: str
