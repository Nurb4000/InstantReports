"""Unit tests for the query builder optimizer."""
from __future__ import annotations

from app.services.query_builder.config import (
    JoinConfig,
    QueryConfig,
    SchemaColumn,
    SchemaResponse,
    SchemaTable,
    SelectColumn,
    WhereFilter,
)
from app.services.query_builder.optimizer import analyze_query, optimize_query


def _sample_config() -> QueryConfig:
    return QueryConfig(
        select=[],
        from_tables=["orders"],
        joins=[
            JoinConfig(
                join_type="INNER",
                table="order_details",
                on_left_table="orders",
                on_left_column="order_id",
                on_right_table="order_details",
                on_right_column="some_col",
            )
        ],
        where=[WhereFilter(field="orders.customer_id", operator="=", value=5)],
    )


def _schema_with_indexes() -> SchemaResponse:
    return SchemaResponse(
        tables=[
            SchemaTable(
                name="orders",
                columns=[
                    SchemaColumn(name="order_id", data_type="int", is_primary_key=True),
                    SchemaColumn(
                        name="customer_id",
                        data_type="int",
                        is_foreign_key=True,
                        foreign_key_table="customers",
                        foreign_key_column="customer_id",
                    ),
                ],
            ),
            SchemaTable(
                name="order_details",
                columns=[SchemaColumn(name="some_col", data_type="int")],
            ),
        ],
        connection_name="test",
    )


def test_flags_select_star_and_missing_where():
    config = QueryConfig(select=[], from_tables=["orders"])
    suggestions = analyze_query(config)
    codes = {s["code"] for s in suggestions}
    assert "select_star" in codes
    assert "missing_where" in codes


def test_group_by_without_aggregation_flagged():
    config = QueryConfig(
        select=[SelectColumn(table="orders", column="customer_id")],
        from_tables=["orders"],
        group_by=["orders.customer_id"],
    )
    codes = {s["code"] for s in analyze_query(config)}
    assert "group_by_no_agg" in codes


def test_join_columns_are_indexed_when_pk_or_fk_present():
    cfg = _sample_config()
    suggestions = analyze_query(cfg, _schema_with_indexes())
    indexed_cols = {s["column"] for s in suggestions if s["code"] == "missing_index"}
    # order_id (PK) and customer_id (FK) should be skipped; some_col is not indexed.
    assert "order_id" not in indexed_cols
    assert "customer_id" not in indexed_cols
    assert "some_col" in indexed_cols


def test_higher_severity_suggestions_come_first():
    suggestions = analyze_query(_sample_config(), _schema_with_indexes())
    severities = [s["severity"] for s in suggestions]
    order = {"high": 0, "medium": 1, "low": 2}
    sorted_orders = [order[s] for s in severities]
    assert sorted_orders == sorted(sorted_orders)


def test_optimize_query_wrapper_matches_analyze_query():
    cfg = _sample_config()
    assert optimize_query(cfg) == analyze_query(cfg)
