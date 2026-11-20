"""Tests for driver-level read-only query enforcement (feature-gap #7).

Every SQL connector executes a designer-supplied query string against the
connected database using the connection's stored credentials. Without a guard a
report definition could carry an INSERT/UPDATE/DELETE/DROP statement and mutate
or destroy data through that connection. These helpers restrict every SQL
connector to a single read-only SELECT before the driver is touched.

Coverage:
* The shared ``validate_read_only_query`` allow/reject matrix (core logic).
* Each SQL connector rejects DML/DDL/multi-statement queries via ``execute_query``
  -- this raises :class:`ReadOnlyQueryError` *before* any driver import or
  network call, so it holds even where the optional DB driver is not installed.
* A valid SELECT reaches the PostgreSQL driver (passthrough verified by mocking
  ``asyncpg.connect``).
"""

import pytest

from app.services.connectors.postgresql import PostgreSQLConnector
from app.services.connectors.read_only import ReadOnlyQueryError, validate_read_only_query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM users",
        "select id, name from t where id = 1",  # lowercase keyword
        "SELECT a, COUNT(*) FROM t GROUP BY a ORDER BY a",
        "SELECT * FROM t UNION SELECT * FROM u",
        "SELECT * FROM t WHERE name = 'O''Brien'",  # escaped quote, no comment char
    ],
)
def test_valid_selects_are_allowed(query):
    """Read-only SELECTs (any case, with clauses/unions) pass validation."""
    # Should not raise.
    validate_read_only_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t (id int)",
        "ALTER TABLE t ADD COLUMN c int",
        "TRUNCATE TABLE t",
        "SELECT * FROM t; DROP TABLE t",  # multiple statements
        "-- drop table t\nSELECT 1",  # leading comment
        "SELECT 1 /* drop table t */ FROM y",  # inline comment
        "SELECT * # comment\nFROM x",  # hash comment
        "EXEC xp_cmdshell 'dir'",
        "GRANT SELECT ON t TO bob",
    ],
)
def test_dangerous_queries_are_rejected(query):
    """DML/DDL, multi-statement, comments, and control statements are rejected."""
    with pytest.raises(ReadOnlyQueryError):
        validate_read_only_query(query)


@pytest.mark.asyncio
async def test_postgresql_rejects_non_select_before_connecting():
    """A non-SELECT query raises ReadOnlyQueryError without touching the driver."""
    connector = PostgreSQLConnector()
    with pytest.raises(ReadOnlyQueryError):
        await connector.execute_query({"host": "h", "database": "d"}, "DELETE FROM t")


@pytest.mark.asyncio
async def test_mysql_rejects_non_select_before_connecting():
    """MySQL rejects a write statement before importing asyncmy/connecting."""
    from app.services.connectors.mysql import MySQLConnector

    with pytest.raises(ReadOnlyQueryError):
        await MySQLConnector().execute_query({"host": "h", "database": "d"}, "INSERT INTO t VALUES (1)")


@pytest.mark.asyncio
async def test_sqlserver_rejects_non_select_before_connecting():
    """SQL Server rejects a DROP before importing pymssql/connecting."""
    from app.services.connectors.sqlserver import SQLServerConnector

    with pytest.raises(ReadOnlyQueryError):
        await SQLServerConnector().execute_query({"host": "h", "database": "d"}, "DROP TABLE t")


@pytest.mark.asyncio
async def test_odbc_rejects_non_select_before_connecting():
    """ODBC rejects a multi-statement query before importing aioodbc/connecting."""
    from app.services.connectors.odbc import ODBCCConnector

    with pytest.raises(ReadOnlyQueryError):
        await ODBCCConnector().execute_query({"dsn": "dsn"}, "SELECT 1; DELETE FROM t")


@pytest.mark.asyncio
async def test_postgresql_valid_select_reaches_driver(monkeypatch):
    """A valid SELECT passes validation and is handed to the driver unchanged."""
    from unittest.mock import AsyncMock

    import asyncpg

    mock_connect = AsyncMock()

    async def _connect(**kwargs):
        mock_conn = AsyncMock()
        # asyncpg's conn.fetch is awaitable and returns row-like objects that
        # support dict(row); emulate that with plain dicts.
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "name": "a"}])
        return mock_conn

    mock_connect.side_effect = _connect
    monkeypatch.setattr(asyncpg, "connect", mock_connect)

    connector = PostgreSQLConnector()
    df = await connector.execute_query(
        {"host": "h", "database": "d", "user": "u", "password": "p"},
        "SELECT id, name FROM users",
    )

    mock_connect.assert_awaited()  # driver was reached with the valid query
    assert list(df.columns) == ["id", "name"]
    assert df.iloc[0]["name"] == "a"


@pytest.mark.asyncio
async def test_fetch_element_data_propagates_read_only_error(monkeypatch):
    """A rejected query surfaces as ReadOnlyQueryError instead of empty data."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.report import rendering

    class _BadConnector:
        async def execute_query(self, config, query, parameters=None):
            raise ReadOnlyQueryError("Query must be a SELECT statement, got: 'INSERT'")

    definition = {
        "layout": {"sections": [{"elements": [{"type": "table", "properties": {"query": "INSERT INTO t VALUES (1)"}}]}]},
        "data_sources": [{"connection_id": "00000000-0000-0000-0000-000000000001"}],
    }

    # db.execute resolves the DataConnection row; give it one pointing at our bad connector.
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock(connector_type="postgresql", config={})
    mock_db.execute = AsyncMock(return_value=mock_result)

    monkeypatch.setattr(rendering, "get_connector", lambda ctype: _BadConnector())

    with pytest.raises(ReadOnlyQueryError):
        await rendering.fetch_element_data(definition, db=mock_db, label="test")
