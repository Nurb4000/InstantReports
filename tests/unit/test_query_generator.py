"""Unit tests for the SQL generator and query-config to_sql() rendering."""
from __future__ import annotations

from app.services.query_builder.config import (
    Aggregation,
    JoinConfig,
    OrderByField,
    QueryConfig,
    SelectColumn,
    WhereFilter,
)
from app.services.query_builder.generator import (
    SQLGenerator,
    generate_sql,
    validate_query,
)


def test_select_column_plain():
    col = SelectColumn(table="orders", column="order_id")
    assert col.to_sql() == "orders.order_id"


def test_select_column_aggregated():
    col = SelectColumn(table="orders", column="amount", aggregation=Aggregation.SUM)
    assert col.to_sql() == "SUM(orders.amount)"


def test_select_column_with_alias():
    col = SelectColumn(table="orders", column="amount", aggregation=Aggregation.SUM, alias="total")
    assert col.to_sql() == "SUM(orders.amount) AS total"


def test_join_config_to_sql_inner():
    join = JoinConfig(
        join_type="INNER",
        table="order_details",
        on_left_table="orders",
        on_left_column="order_id",
        on_right_table="order_details",
        on_right_column="order_id",
    )
    assert join.to_sql() == (
        "INNER JOIN order_details ON orders.order_id = order_details.order_id"
    )


def test_join_config_to_sql_left():
    join = JoinConfig(
        join_type="LEFT",
        table="order_details",
        on_left_table="orders",
        on_left_column="order_id",
        on_right_table="order_details",
        on_right_column="order_id",
    )
    assert "LEFT JOIN" in join.to_sql()


def test_where_filter_equals():
    where = WhereFilter(field="orders.customer_id", operator="=", value=5)
    assert where.to_sql() == "orders.customer_id = '5'"


def test_where_filter_in_list():
    where = WhereFilter(field="orders.status", operator="IN", value=["a", "b"])
    assert where.to_sql() == "orders.status IN ('a', 'b')"


def test_where_filter_between():
    where = WhereFilter(field="orders.amount", operator="BETWEEN", value=[10, 20])
    assert where.to_sql() == "orders.amount BETWEEN '10' AND '20'"


def test_where_filter_like():
    where = WhereFilter(field="orders.note", operator="LIKE", value="foo")
    assert where.to_sql() == "orders.note LIKE 'foo'"


def test_where_filter_is_null_and_not_null():
    # IS NULL / IS NOT NULL filters carry value=None (see sql_parser._parse_where_condition)
    assert WhereFilter(field="t.a", operator="IS NULL", value=None).to_sql() == "t.a IS NULL"
    assert WhereFilter(field="t.a", operator="IS NOT NULL", value=None).to_sql() == "t.a IS NOT NULL"


def test_query_config_where_honors_per_filter_logic():
    # Regression: multiple WHERE filters must be joined using each filter's own
    # logic operator, not a single shared operator.
    q = QueryConfig(
        where=[
            WhereFilter(field="a", operator="=", value=1),
            WhereFilter(field="b", operator="=", value=2, logic="OR"),
            WhereFilter(field="c", operator="=", value=3, logic="AND"),
        ]
    )
    sql = q.to_sql().splitlines()[-1]
    assert sql == "WHERE a = '1' OR b = '2' AND c = '3'"


def test_query_config_single_where_filter():
    q = QueryConfig(where=[WhereFilter(field="a", operator="=", value=1, logic="OR")])
    assert q.to_sql().splitlines()[-1] == "WHERE a = '1'"


def test_order_by_field_to_sql():
    assert OrderByField(field="orders.order_id", direction="DESC").to_sql() == "orders.order_id DESC"


def test_generate_select_empty_returns_star():
    assert SQLGenerator.generate_select([]) == "*"


def test_generate_select_joins_columns():
    cols = [
        SelectColumn(table="orders", column="order_id"),
        SelectColumn(table="orders", column="amount", aggregation=Aggregation.COUNT),
    ]
    assert SQLGenerator.generate_select(cols) == "orders.order_id, COUNT(orders.amount)"


def test_generate_from():
    assert SQLGenerator.generate_from([]) == ""
    assert SQLGenerator.generate_from(["orders"]) == "FROM orders"


def test_generate_joins():
    joins = [
        JoinConfig(
            join_type="INNER",
            table="d",
            on_left_table="o",
            on_left_column="id",
            on_right_table="d",
            on_right_column="id",
        )
    ]
    assert SQLGenerator.generate_joins(joins) == "INNER JOIN d ON o.id = d.id"


def test_generate_where_single():
    filters = [WhereFilter(field="orders.a", operator="=", value=1)]
    assert SQLGenerator.generate_where(filters) == "WHERE orders.a = '1'"


def test_generate_where_multiple_and_or():
    filters = [
        WhereFilter(field="orders.a", operator="=", value=1),
        WhereFilter(field="orders.b", operator="=", value=2, logic="OR"),
    ]
    assert SQLGenerator.generate_where(filters) == "WHERE orders.a = '1' OR orders.b = '2'"


def test_generate_group_by_and_order_by():
    assert SQLGenerator.generate_group_by([]) == ""
    assert SQLGenerator.generate_group_by(["orders.customer_id"]) == "GROUP BY orders.customer_id"
    assert SQLGenerator.generate_order_by([OrderByField(field="orders.x")]) == "ORDER BY orders.x ASC"


def test_generate_full_query():
    config = QueryConfig(
        select=[SelectColumn(table="orders", column="order_id")],
        from_tables=["orders"],
        where=[WhereFilter(field="orders.status", operator="=", value="open")],
        order_by=[OrderByField(field="orders.order_id", direction="DESC")],
    )
    sql = SQLGenerator.generate(config)
    assert sql.splitlines()[0] == "SELECT orders.order_id"
    assert "FROM orders" in sql
    assert "WHERE orders.status = 'open'" in sql
    assert "ORDER BY orders.order_id DESC" in sql


def test_generate_full_query_without_optional_clauses():
    config = QueryConfig(select=[SelectColumn(table="t", column="a")], from_tables=["t"])
    sql = SQLGenerator.generate(config)
    assert sql == "SELECT t.a\nFROM t"


def test_validate_config_valid():
    config = QueryConfig(
        select=[SelectColumn(table="orders", column="order_id")],
        from_tables=["orders"],
    )
    is_valid, errors = SQLGenerator.validate_config(config)
    assert is_valid is True
    assert errors == []


def test_validate_config_requires_from_table():
    config = QueryConfig(select=[SelectColumn(table="t", column="a")])
    _, errors = SQLGenerator.validate_config(config)
    assert any("FROM clause" in e for e in errors)


def test_validate_config_join_left_table_must_be_in_from():
    config = QueryConfig(
        select=[SelectColumn(table="orders", column="order_id")],
        from_tables=["orders"],
        joins=[
            JoinConfig(
                join_type="INNER",
                table="customers",
                on_left_table="missing",
                on_left_column="id",
                on_right_table="customers",
                on_right_column="id",
            )
        ],
    )
    _, errors = SQLGenerator.validate_config(config)
    assert any("not in FROM clause" in e for e in errors)


def test_validate_config_join_right_table_must_match():
    config = QueryConfig(
        select=[SelectColumn(table="orders", column="order_id")],
        from_tables=["orders"],
        joins=[
            JoinConfig(
                join_type="INNER",
                table="customers",
                on_left_table="orders",
                on_left_column="customer_id",
                on_right_table="suppliers",
                on_right_column="id",
            )
        ],
    )
    _, errors = SQLGenerator.validate_config(config)
    assert any("must match JOIN table" in e for e in errors)


def test_validate_config_field_must_be_qualified():
    config = QueryConfig(
        select=[SelectColumn(table="orders", column="order_id")],
        from_tables=["orders"],
        where=[WhereFilter(field="customer_id", operator="=", value=1)],
    )
    _, errors = SQLGenerator.validate_config(config)
    assert any("table.column" in e for e in errors)


def test_convenience_functions():
    config = QueryConfig(select=[SelectColumn(table="t", column="a")], from_tables=["t"])
    assert generate_sql(config).startswith("SELECT t.a")
    is_valid, _ = validate_query(config)
    assert is_valid is True
