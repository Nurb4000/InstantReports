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

    def to_sql(self) -> str:
        """Generate SQL for this select column."""
        if self.aggregation and self.aggregation != Aggregation.NONE:
            col_expr = f"{self.aggregation.value}({self.table}.{self.column})"
        else:
            col_expr = f"{self.table}.{self.column}"

        if self.alias:
            return f"{col_expr} AS {self.alias}"
        return col_expr


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

        if isinstance(self.value, list):
            values_str = ", ".join(f"'{v}'" for v in self.value)
            return f"{self.field} IN ({values_str})"

        if self.operator == Operator.LIKE:
            return f"{self.field} LIKE '{self.value}'"

        if (
            self.operator == Operator.BETWEEN
            and isinstance(self.value, (list, tuple))
            and len(self.value) == 2
        ):
            return f"{self.field} BETWEEN '{self.value[0]}' AND '{self.value[1]}'"

        return f"{self.field} {self.operator.value} '{self.value}'"


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
            select_cols = [col.to_sql() for col in self.select]
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
            if len(where_clauses) > 1:
                logic = f" {self.where[0].logic} "
                sql_parts.append(f"WHERE {logic.join(where_clauses)}")
            else:
                sql_parts.append(f"WHERE {where_clauses[0]}")

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
