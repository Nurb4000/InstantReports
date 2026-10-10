"""Regression tests for query-builder SQL-injection guards (round-8e).

The visual query builder builds a QueryConfig client-side and executes it via
``/test`` against a real database connection. Table/column names, aliases, join
conditions, ORDER BY direction and GROUP BY items are interpolated *unquoted*
into the generated SQL, so a crafted config could inject SQL (e.g. an alias of
``1; DROP TABLE t``). Identifiers are now validated at the model boundary, which
the /test endpoint enforces by rejecting malformed bodies with 422 before any
query runs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.query_builder.config import (
    JoinConfig,
    OrderByField,
    QueryConfig,
    SelectColumn,
    WhereFilter,
)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SelectColumn(table="orders", column="id; DROP"),
        lambda: SelectColumn(table="t", column="a", alias="1; DROP TABLE x"),
        lambda: JoinConfig(
            table="t;--", on_left_table="a", on_left_column="id",
            on_right_table="b", on_right_column="id",
        ),
        lambda: WhereFilter(field="t.n; DROP", operator="=", value=1),
        # ``logic`` is emitted unquoted; anything other than AND/OR is injection.
        lambda: WhereFilter(field="t.a", operator="=", value=1, logic="OR 1=1 UNION SELECT * FROM users --"),
        lambda: WhereFilter(field="t.a", operator="=", value=1, logic="; DROP TABLE users"),
        lambda: OrderByField(field="t.a", direction="ASC; DROP TABLE t"),
        lambda: QueryConfig(from_tables=["orders;--"], select=[SelectColumn(table="a", column="b")]),
        lambda: QueryConfig(group_by=["x; DROP"]),
    ],
)
def test_malicious_identifiers_rejected(factory):
    with pytest.raises(ValidationError):
        factory()


def test_valid_identifiers_accepted():
    # Normal dotted identifiers and simple aliases must still validate.
    SelectColumn(table="orders", column="order_id")
    SelectColumn(table="orders", column="amount", alias="total")
    OrderByField(field="orders.order_id", direction="desc")
    QueryConfig(from_tables=["public.orders"], group_by=["orders.customer_id"])


def test_valid_where_logic_accepted_and_normalized():
    # AND/OR (any case) is accepted and normalized to upper case; the value is
    # emitted verbatim into SQL, so the normal form must be canonical.
    assert WhereFilter(field="t.a", operator="=", value=1).logic == "AND"
    assert WhereFilter(field="t.a", operator="=", value=1, logic="or").logic == "OR"
    cfg = QueryConfig(
        from_tables=["orders"],
        select=[SelectColumn(table="orders", column="id")],
        where=[
            WhereFilter(field="orders.id", operator="=", value=1),
            WhereFilter(field="orders.id", operator="=", value=2, logic="or"),
        ],
    )
    assert "WHERE orders.id = '1' OR orders.id = '2'" in cfg.to_sql()


@pytest.mark.asyncio
async def test_test_endpoint_rejects_injected_config(client, test_user):
    """A malicious config is rejected at request-validation time (422) before the
    query ever reaches the database."""
    import uuid


    # Authenticate as the designer user created by the test_user fixture.
    res = await client.post(
        "/auth/login",
        data={"email": test_user.email, "password": "password123"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    client.headers["Cookie"] = f"access_token={res.cookies.get('access_token')}"

    # Send the malicious config as raw JSON so it is validated server-side by
    # FastAPI/pydantic (a locally-constructed QueryConfig would raise here).
    # connection_id is a query param; a bogus UUID means an unguarded endpoint
    # would accept the body and reach execution (404 on missing connection),
    # whereas the identifier guard rejects it up front with 422.
    malicious_body = {
        "from_tables": ["orders"],
        "select": [
            {"table": "orders", "column": "id", "alias": "1; DROP TABLE orders"}
        ],
    }
    resp = await client.post(
        f"/api/query-builder/test?connection_id={uuid.uuid4()}",
        json=malicious_body,
    )
    assert resp.status_code == 422
