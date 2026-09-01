"""Database adapters for the SQL Query Builder.

The query builder previously only supported PostgreSQL via ``asyncpg``. This
module provides a connector-type-aware facade so the same ``SchemaResponse``
shape and test-query execution work across PostgreSQL, MySQL, and SQLite.

Each backend is fetched lazily (imported inside the coroutine) so that missing
drivers do not break import time or the other backends.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.query_builder.config import SchemaColumn, SchemaResponse, SchemaTable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# PostgreSQL
# --------------------------------------------------------------------------- #
async def fetch_postgres_schema(config: dict[str, Any]) -> SchemaResponse:
    import asyncpg

    schema_name = config.get("schema", "public")
    conn = await asyncpg.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["user"],
        password=config["password"],
        database=config["database"],
    )
    try:
        table_rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = $1 AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            schema_name,
        )

        tables: list[SchemaTable] = []
        for table_row in table_rows:
            table_name = table_row["table_name"]

            columns_query = """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """
            cols = await conn.fetch(columns_query, schema_name, table_name)
            col_list = [
                SchemaColumn(
                    name=c["column_name"],
                    data_type=c["data_type"],
                    nullable=c["is_nullable"] == "YES",
                )
                for c in cols
            ]

            pk_cols = await conn.fetch(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = $1 AND tc.table_name = $2
                  AND tc.constraint_type = 'PRIMARY KEY'
                """,
                schema_name,
                table_name,
            )
            for c in col_list:
                if any(pk["column_name"] == c.name for pk in pk_cols):
                    c.is_primary_key = True

            fk_cols = await conn.fetch(
                """
                SELECT kcu.column_name, ccu.table_name AS ref_table,
                       ccu.column_name AS ref_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                 AND tc.table_schema = ccu.table_schema
                WHERE tc.table_schema = $1 AND tc.table_name = $2
                  AND tc.constraint_type = 'FOREIGN KEY'
                """,
                schema_name,
                table_name,
            )
            for c in col_list:
                fk = next((f for f in fk_cols if f["column_name"] == c.name), None)
                if fk:
                    c.is_foreign_key = True
                    c.foreign_key_table = fk["ref_table"]
                    c.foreign_key_column = fk["ref_column"]

            tables.append(SchemaTable(name=table_name, columns=col_list))

        return SchemaResponse(tables=tables, connection_name=config.get("name", "PostgreSQL"))
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# SQLite
# --------------------------------------------------------------------------- #
def _sqlite_path(config: dict[str, Any]) -> str:
    return config.get("path") or config.get("filepath") or config.get("database") or ""


async def fetch_sqlite_schema(config: dict[str, Any]) -> SchemaResponse:
    import aiosqlite

    path = _sqlite_path(config)
    if not path:
        raise ValueError("SQLite connection config requires a 'database'/'path' file")

    conn = await aiosqlite.connect(path)
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        rows = await cur.fetchall()

        tables: list[SchemaTable] = []
        for (table_name,) in rows:
            cur = await conn.execute(f"PRAGMA table_info('{table_name}')")
            pragma_cols = await cur.fetchall()  # cid, name, type, notnull, dflt, pk

            cur = await conn.execute(f"PRAGMA foreign_key_list('{table_name}')")
            fk_rows = await cur.fetchall()  # id, seq, table, from, to, ...
            fk_from_cols = {r[3] for r in fk_rows}
            fk_map = {r[3]: (r[2], r[4]) for r in fk_rows}

            col_list: list[SchemaColumn] = []
            for _cid, name, dtype, _notnull, _dflt, pk in pragma_cols:
                is_fk = name in fk_from_cols
                fk = fk_map.get(name)
                col_list.append(
                    SchemaColumn(
                        name=name,
                        data_type=dtype or "TEXT",
                        nullable=bool(pk) is False,
                        is_primary_key=bool(pk),
                        is_foreign_key=is_fk,
                        foreign_key_table=fk[0] if is_fk else None,
                        foreign_key_column=fk[1] if is_fk else None,
                    )
                )
            tables.append(SchemaTable(name=table_name, columns=col_list))

        return SchemaResponse(tables=tables, connection_name="SQLite")
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# MySQL
# --------------------------------------------------------------------------- #
async def fetch_mysql_schema(config: dict[str, Any]) -> SchemaResponse:
    asyncmy = __import__("asyncmy")

    database = config.get("database", "")
    schema_name = config.get("schema", database)
    conn = await asyncmy.connect(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 3306)),
        database=database,
        user=config.get("user", ""),
        password=config.get("password", ""),
    )

    try:
        tables = []
        rows = await conn.execute(
            """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """,
            schema_name,
        )
        table_names = [r[0] for r in await rows.fetchall()]

        for table_name in table_names:
            cols_row = await conn.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                schema_name,
                table_name,
            )
            col_defs = [(r[0], r[1], r[2]) for r in await cols_row.fetchall()]

            pk_row = await conn.execute(
                """
                SELECT kcu.COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                 AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                WHERE tc.TABLE_SCHEMA = %s AND tc.TABLE_NAME = %s
                  AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                """,
                schema_name,
                table_name,
            )
            pk_cols = {r[0] for r in await pk_row.fetchall()}

            fk_row = await conn.execute(
                """
                SELECT kcu.COLUMN_NAME, ccu.TABLE_NAME AS ref_table,
                       ccu.COLUMN_NAME AS ref_column
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                 AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
                  ON tc.CONSTRAINT_NAME = ccu.CONSTRAINT_NAME
                WHERE tc.TABLE_SCHEMA = %s AND tc.TABLE_NAME = %s
                  AND tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
                """,
                schema_name,
                table_name,
            )
            fk_map = {(r[0], r[1], r[2]) for r in await fk_row.fetchall()}

            col_list: list[SchemaColumn] = []
            for name, dtype, nullable in col_defs:
                fk_match = next((v for (k, v, w) in fk_map if k == name), None)
                col_list.append(
                    SchemaColumn(
                        name=name,
                        data_type=dtype,
                        nullable=nullable == "YES",
                        is_primary_key=name in pk_cols,
                        is_foreign_key=fk_match is not None,
                        foreign_key_table=fk_match[0] if fk_match else None,
                        foreign_key_column=fk_match[1] if fk_match else None,
                    )
                )
            tables.append(SchemaTable(name=table_name, columns=col_list))

        return SchemaResponse(tables=tables, connection_name=config.get("name", "MySQL"))
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
async def execute_query(
    connector_type: str,
    config: dict[str, Any],
    sql: str,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Run a read query and return rows as a list of dicts."""
    ct = (connector_type or "").lower()

    if ct == "postgresql":
        import asyncpg

        conn = await asyncpg.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["database"],
        )
        try:
            rows = await conn.fetch(sql)
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    if ct == "sqlite":
        import aiosqlite

        path = _sqlite_path(config)
        if not path:
            raise ValueError("SQLite connection config requires a 'database'/'path' file")
        conn = await aiosqlite.connect(path)
        try:
            cur = await conn.execute(sql)
            names = [d[0] for d in cur.description] if cur.description else []
            rows = await cur.fetchall()
            result = [dict(zip(names, r)) for r in rows]
            if limit is not None:
                result = result[:limit]
            return result
        finally:
            await conn.close()

    if ct == "mysql":
        asyncmy = __import__("asyncmy")
        conn = await asyncmy.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            database=config.get("database", ""),
            user=config.get("user", ""),
            password=config.get("password", ""),
        )
        try:
            cur = await conn.execute(sql)
            names = [d[0] for d in cur.description] if cur.description else []
            rows = await cur.fetchall()
            result = [dict(zip(names, r)) for r in rows]
            if limit is not None:
                result = result[:limit]
            return result
        finally:
            await conn.close()

    raise ValueError(f"Unsupported connector type for query builder: {connector_type!r}")


ADAPTERS = {
    "postgresql": fetch_postgres_schema,
    "postgres": fetch_postgres_schema,
    "sqlite": fetch_sqlite_schema,
    "mysql": fetch_mysql_schema,
}


async def fetch_schema(
    connector_type: str, config: dict[str, Any]
) -> Optional[SchemaResponse]:
    """Fetch a normalized :class:`SchemaResponse` for the given connector."""
    adapter = ADAPTERS.get((connector_type or "").lower())
    if not adapter:
        logger.warning("No query-builder schema adapter for connector %r", connector_type)
        return None
    return await adapter(config)
