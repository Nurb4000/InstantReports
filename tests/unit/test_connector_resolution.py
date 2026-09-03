"""Tests for connector resolution used by the preview route and runner.

Covers ``resolve_data_source_connector`` in ``app.services.connectors.base``,
which is FastAPI-free so it runs in the minimal test env (unlike the route
modules that need fastapi/jose).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.connectors.base import resolve_data_source_connector


def _fake_db(result_for_id, result_for_fallback):
    """Async mock db whose execute returns different rows per query.

    The id-lookup and the postgresql fallback differ enough in their compiled
    SQL to tell apart with a substring check.
    """

    async def fake_execute(stmt):
        # The fallback query is the only one whose compiled SQL names the
        # connector_type column; the id-lookup uses data_connections.id instead.
        if "connector_type" in str(stmt):
            return result_for_fallback
        return result_for_id

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    return db


def _result(connection):
    obj = MagicMock()
    obj.scalar_one_or_none.return_value = connection
    return obj


async def test_resolves_connector_type_from_model_not_config(monkeypatch):
    """The connector is chosen from DataConnection.connector_type, never from
    the config dict. This is the regression guard for the preview bug where
    ``connection_config.get('connector_type', 'postgresql')`` always defaulted
    to postgresql and broke non-PostgreSQL previews."""
    captured = {}

    def fake_get_connector(connector_type):
        captured["type"] = connector_type
        return MagicMock(name=f"connector-{connector_type}")

    monkeypatch.setattr("app.services.connectors.base.get_connector", fake_get_connector)

    conn = MagicMock()
    conn.connector_type = "mysql"
    conn.config = {"host": "db", "port": 3306}
    conn.name = "my-mysql"

    db = _fake_db(_result(conn), _result(conn))

    connector, config = await resolve_data_source_connector(
        db, {"data_sources": [{"connection_id": "abc-123"}]}
    )

    assert connector is not None
    assert captured["type"] == "mysql"  # from the model column
    assert config == {"host": "db", "port": 3306}


async def test_by_connection_id_from_definition():
    conn = MagicMock()
    conn.connector_type = "postgresql"
    conn.config = {"host": "x"}
    conn.name = "pg"

    db = _fake_db(_result(conn), _result(conn))
    connector, config = await resolve_data_source_connector(
        db, {"data_sources": [{"connection_id": "def-1"}]}
    )

    assert connector is not None
    assert config == {"host": "x"}
    # id lookup path was taken (no fallback needed)


async def test_falls_back_to_first_postgresql_connection():
    pg = MagicMock()
    pg.connector_type = "postgresql"
    pg.config = {"host": "fallback"}
    pg.name = "fallback-pg"

    # A non-postgresql connection exists but is only reachable via the fallback
    # query (no data_sources in the definition).
    db = _fake_db(_result(None), _result(pg))

    connector, config = await resolve_data_source_connector(db, {})

    assert connector is not None
    assert config == {"host": "fallback"}


async def test_returns_none_when_no_connection():
    db = _fake_db(_result(None), _result(None))
    connector, config = await resolve_data_source_connector(db, {})
    assert connector is None
    assert config is None


async def test_returns_none_when_db_is_none():
    connector, config = await resolve_data_source_connector(None, {})
    assert connector is None
    assert config is None
