"""Unit tests for the SQL -> QueryConfig parser."""
from __future__ import annotations

from app.services.query_builder.config import Aggregation, JoinType, Operator
from app.services.query_builder.sql_parser import parse_sql_to_config


def test_empty_string():
    cfg = parse_sql_to_config("")
    assert cfg.select == []
    assert cfg.from_tables == []


def test_select_star_from_single_table():
    cfg = parse_sql_to_config("SELECT * FROM orders")
    assert cfg.select == []
    assert cfg.from_tables == ["orders"]


def test_qualified_columns_with_aggregation_and_alias():
    sql = "SELECT c.name, COUNT(o.id) AS order_count FROM customers c"
    cfg = parse_sql_to_config(sql)
    assert cfg.from_tables == ["customers"]
    cols = {c.column: c for c in cfg.select}
    assert cols["name"].table == "c"
    agg_col = next(c for c in cfg.select if c.alias == "order_count")
    assert agg_col.aggregation == Aggregation.COUNT
    assert agg_col.alias == "order_count"


def test_join_parsed():
    sql = (
        "SELECT orders.id FROM orders "
        "INNER JOIN customers ON customers.id = orders.customer_id"
    )
    cfg = parse_sql_to_config(sql)
    assert len(cfg.joins) == 1
    join = cfg.joins[0]
    assert join.join_type == JoinType.INNER
    assert join.table == "customers"
    assert join.on_left_table == "customers"
    assert join.on_right_table == "orders"


def test_where_with_and():
    sql = "SELECT a FROM t WHERE a.x = 1 AND a.y > 10"
    cfg = parse_sql_to_config(sql)
    assert len(cfg.where) == 2
    assert cfg.where[0].operator == Operator.EQUALS
    assert cfg.where[0].value == "1"
    assert cfg.where[1].operator == Operator.GREATER_THAN
    assert cfg.where[1].logic == "AND"


def test_where_preserves_per_operator_logic():
    """Mixed AND/OR clauses must keep each condition's own operator.

    Regression: the parser tagged every condition with the *last* logic
    operator seen, so ``A AND B OR C`` round-tripped as ``A OR B OR C`` and
    silently changed the query's semantics.
    """
    sql = "SELECT a FROM t WHERE a.x = 1 AND a.y = 2 OR a.z = 3"
    cfg = parse_sql_to_config(sql)
    assert [w.logic for w in cfg.where] == ["AND", "AND", "OR"]


def test_where_is_null_and_in():
    sql = "SELECT a FROM t WHERE a.x IS NULL AND a.y IN (1, 2, 3)"
    cfg = parse_sql_to_config(sql)
    ops = {f.field: f.operator for f in cfg.where}
    assert ops["a.x"] == Operator.IS_NULL
    assert cfg.where[1].value == ["1", "2", "3"]


def test_group_by_and_order_by():
    sql = (
        "SELECT a.x, SUM(a.y) FROM t "
        "GROUP BY a.x ORDER BY a.x DESC"
    )
    cfg = parse_sql_to_config(sql)
    assert cfg.group_by == ["a.x"]
    assert len(cfg.order_by) == 1
    assert cfg.order_by[0].field == "a.x"
    assert cfg.order_by[0].direction == "DESC"


def test_roundtrip_sql_equivalence():
    sql = (
        "SELECT orders.id, customers.name FROM orders "
        "INNER JOIN customers ON customers.id = orders.customer_id "
        "WHERE customers.active = 1 "
        "ORDER BY orders.id ASC"
    )
    cfg = parse_sql_to_config(sql)
    regenerated = cfg.to_sql()
    assert "SELECT" in regenerated
    assert "FROM orders" in regenerated
    assert "INNER JOIN customers" in regenerated
    assert "customers.active = '1'" in regenerated
    assert "ORDER BY orders.id ASC" in regenerated


def test_trailing_semicolon_and_newlines():
    sql = "SELECT *\nFROM orders\n;"
    cfg = parse_sql_to_config(sql)
    assert cfg.from_tables == ["orders"]


def test_between_roundtrip_preserved():
    sql = "SELECT orders.id FROM orders WHERE orders.amount BETWEEN '10' AND '20'"
    cfg = parse_sql_to_config(sql)
    assert len(cfg.where) == 1
    assert cfg.where[0].operator.value == "BETWEEN"
    regenerated = cfg.to_sql()
    assert "BETWEEN '10' AND '20'" in regenerated


def test_between_among_multiple_conditions():
    # The connector AND inside BETWEEN must not be split as a logic operator.
    sql = (
        "SELECT orders.id FROM orders "
        "WHERE orders.status = 'open' AND orders.amount BETWEEN '10' AND '20' AND orders.qty > 5"
    )
    cfg = parse_sql_to_config(sql)
    assert len(cfg.where) == 3
    operators = [f.operator.value for f in cfg.where]
    assert operators == ["=", "BETWEEN", ">"]
