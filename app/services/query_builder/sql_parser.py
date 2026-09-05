"""Best-effort SQL -> QueryConfig parser for the query builder.

Used to convert AI-generated (or pasted) SQL back into a structured
:class:`QueryConfig` so it can be edited in the visual builder. Handles the
common subset of SQL that the builder itself produces: SELECT (with optional
aggregates and aliases), FROM, JOIN ... ON, WHERE (AND/OR), GROUP BY and
ORDER BY. It is intentionally forgiving rather than a full SQL grammar.
"""

from __future__ import annotations

import re

from app.services.query_builder.config import (
    Aggregation,
    JoinConfig,
    JoinType,
    Operator,
    OrderByField,
    QueryConfig,
    SelectColumn,
    WhereFilter,
)

_AGG_PATTERN = re.compile(r"^(COUNT|SUM|AVG|MIN|MAX)\s*\((.*)\)$", re.IGNORECASE)

# Matches: <type> JOIN <table> ON <ltable>.<lcol> = <rtable>.<rcol>
_JOIN_PATTERN = re.compile(
    r"\b(INNER|LEFT|RIGHT|FULL)\s+JOIN\s+(\w+)\s+ON\s+"
    r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)",
    re.IGNORECASE,
)

# Matches: <table>.<column> <operator> <value>
_OPERATOR_PATTERN = re.compile(
    r"^(\w+)\.(\w+)\s*(=|!=|<|<=|>|>=|LIKE)\s*(.+)$",
    re.IGNORECASE,
)

# Logical connectors between WHERE conditions.
_LOGIC_PATTERN = re.compile(r"\b(AND|OR)\b", re.IGNORECASE)


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split ``text`` on ``separator`` (a regex) respecting parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_select(expr: str) -> SelectColumn:
    expr = expr.strip()

    # Strip an optional trailing " AS alias" so aggregates/aliases parse cleanly.
    alias_match = re.search(r"\s+AS\s+(\w+)$", expr, re.IGNORECASE)
    base = expr[:alias_match.start()].strip() if alias_match else expr
    alias = alias_match.group(1) if alias_match else None

    match = _AGG_PATTERN.match(base)
    aggregation = None
    payload = base
    if match:
        aggregation = Aggregation(match.group(1).upper())
        payload = match.group(2).strip()

    if "." in payload:
        table, column = payload.rsplit(".", 1)
    else:
        table, column = "", payload

    return SelectColumn(table=table, column=column, alias=alias, aggregation=aggregation)


def _parse_where_condition(cond: str) -> WhereFilter | None:
    cond = cond.strip()
    if not cond:
        return None

    in_match = re.match(r"^(\w+)\.(\w+)\s+IN\s*\((.*)\)$", cond, re.IGNORECASE)
    if in_match:
        values = [v.strip().strip("'\"") for v in in_match.group(3).split(",") if v.strip()]
        return WhereFilter(field=f"{in_match.group(1)}.{in_match.group(2)}", operator=Operator.IN, value=values)

    between_match = re.match(
        r"^(\w+)\.(\w+)\s+BETWEEN\s+'(.*)'\s+AND\s+'(.*)'$", cond, re.IGNORECASE
    )
    if between_match:
        return WhereFilter(
            field=f"{between_match.group(1)}.{between_match.group(2)}",
            operator=Operator.BETWEEN,
            value=[between_match.group(3), between_match.group(4)],
        )

    is_null_match = re.match(r"^(\w+)\.(\w+)\s+IS\s+(NOT\s+)?NULL$", cond, re.IGNORECASE)
    if is_null_match:
        has_not = bool(is_null_match.group(3))
        operator = Operator.IS_NOT_NULL if has_not else Operator.IS_NULL
        return WhereFilter(field=f"{is_null_match.group(1)}.{is_null_match.group(2)}", operator=operator, value=None)

    op_match = _OPERATOR_PATTERN.match(cond)
    if op_match:
        operator = Operator(op_match.group(3).upper())
        value = op_match.group(4).strip().strip("'\"")
        return WhereFilter(field=f"{op_match.group(1)}.{op_match.group(2)}", operator=operator, value=value)

    return None


def parse_sql_to_config(sql: str) -> QueryConfig:
    """Parse a SQL string into a :class:`QueryConfig`."""
    normalized = re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()
    if not normalized:
        return QueryConfig()

    # FROM tables (everything after FROM up to the next clause keyword).
    from_tables: list[str] = []
    from_match = re.search(r"\bFROM\s+([\w\"]+)", normalized, re.IGNORECASE)
    if from_match:
        from_tables = [from_match.group(1).strip('"')]

    # SELECT clause.
    select_match = re.search(r"\bSELECT\s+(.*?)\s+\bFROM\b", normalized, re.IGNORECASE | re.DOTALL)
    columns: list[SelectColumn] = []
    if select_match:
        select_body = select_match.group(1)
        if select_body.strip() != "*":
            columns = [_parse_select(c) for c in _split_top_level(select_body, ",")]

    # JOINs.
    joins: list[JoinConfig] = []
    for m in _JOIN_PATTERN.finditer(normalized):
        join_type = JoinType(m.group(1).upper())
        joins.append(
            JoinConfig(
                join_type=join_type,
                table=m.group(2),
                on_left_table=m.group(3),
                on_left_column=m.group(4),
                on_right_table=m.group(5),
                on_right_column=m.group(6),
            )
        )

    # WHERE clause (up to GROUP BY / ORDER BY / LIMIT / end).
    where: list[WhereFilter] = []
    where_match = re.search(r"\bWHERE\s+(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)", normalized, re.IGNORECASE | re.DOTALL)
    if where_match:
        body = where_match.group(1).strip()
        if body:
            # Protect "BETWEEN ... AND ..." so the connector AND inside a BETWEEN
            # clause isn't mistaken for a logic operator and dropped.
            protected: list[str] = []

            def _protect_between(match: re.Match) -> str:
                protected.append(match.group(0))
                return f"\x00BETWEEN{len(protected) - 1}\x00"

            protected_body = re.sub(r"(?is)\bBETWEEN\b.*?\bAND\b", _protect_between, body)
            # ``split`` alternates condition / operator: [cond, op, cond, op, ...].
            # Pair each condition with the operator that precedes it so mixed
            # AND/OR clauses survive the round-trip; tagging every condition with
            # the last-seen operator would silently rewrite query semantics.
            logic_parts = _LOGIC_PATTERN.split(protected_body)
            conditions: list[tuple[str, str]] = []
            current_logic = "AND"
            for i, piece in enumerate(logic_parts):
                if i % 2 == 1:
                    current_logic = piece.upper()
                    continue
                if piece.strip():
                    conditions.append((piece.strip(), current_logic))
            for cond, cond_logic in conditions:
                restored = cond
                for idx, original in enumerate(protected):
                    restored = restored.replace(f"\x00BETWEEN{idx}\x00", original)
                parsed = _parse_where_condition(restored)
                if parsed:
                    parsed.logic = cond_logic
                    where.append(parsed)

    # GROUP BY.
    group_by: list[str] = []
    group_match = re.search(r"\bGROUP\s+BY\s+(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)", normalized, re.IGNORECASE | re.DOTALL)
    if group_match:
        group_by = _split_top_level(group_match.group(1), ",")

    # ORDER BY.
    order_by: list[OrderByField] = []
    order_match = re.search(r"\bORDER\s+BY\s+(.+)", normalized, re.IGNORECASE | re.DOTALL)
    if order_match:
        for expr in _split_top_level(order_match.group(1).strip(), ","):
            direction = "DESC" if re.search(r"\bDESC\b", expr, re.IGNORECASE) else "ASC"
            field = re.sub(r"\b(ASC|DESC)\b", "", expr, flags=re.IGNORECASE).strip()
            if field:
                order_by.append(OrderByField(field=field, direction=direction))

    return QueryConfig(
        select=columns,
        from_tables=from_tables,
        joins=joins,
        where=where,
        group_by=group_by,
        order_by=order_by,
    )
