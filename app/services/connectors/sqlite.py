from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.services.connectors.base import DataConnector
from app.services.connectors.read_only import validate_read_only_query

logger = logging.getLogger(__name__)


class SQLiteConnector(DataConnector):
    """Connector for external SQLite database files (``.db`` / ``.sqlite``).

    Lets a report use a local SQLite file as one of several supported data-source
    connectors (postgres / sqlite / mysql / ...). This is a *user data source*,
    not the application's own database, so it is intentionally kept even though the
    test backend and app data layer are PostgreSQL.
    """

    config_fields = [
        {"name": "database", "label": "Database File Path", "type": "text", "default": "", "required": True},
    ]

    @staticmethod
    def _path(config: dict[str, Any]) -> str:
        return config.get("path") or config.get("filepath") or config.get("database") or ""

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            import aiosqlite

            path = self._path(config)
            if not path:
                return False
            conn = await aiosqlite.connect(path)
            try:
                await conn.execute("SELECT 1")
                return True
            finally:
                await conn.close()
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        import aiosqlite

        tables: list[dict[str, Any]] = []
        path = self._path(config)
        if not path:
            return {"tables": tables}

        conn = await aiosqlite.connect(path)
        try:
            cur = await conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            rows = await cur.fetchall()
            for (table_name,) in rows:
                col_cur = await conn.execute(f"PRAGMA table_info('{table_name}')")
                pragma_cols = await col_cur.fetchall()  # cid, name, type, notnull, dflt, pk

                fk_cur = await conn.execute(f"PRAGMA foreign_key_list('{table_name}')")
                fk_rows = await fk_cur.fetchall()
                fk_from_cols = {r[3] for r in fk_rows}
                fk_map = {r[3]: (r[2], r[4]) for r in fk_rows}

                columns: list[dict[str, Any]] = []
                for _cid, name, dtype, _notnull, _dflt, pk in pragma_cols:
                    is_fk = name in fk_from_cols
                    columns.append({
                        "name": name,
                        "type": dtype or "TEXT",
                        "nullable": bool(pk) is False,
                        "is_primary_key": bool(pk),
                        "is_foreign_key": is_fk,
                        "foreign_key_table": fk_map[name][0] if is_fk else None,
                        "foreign_key_column": fk_map[name][1] if is_fk else None,
                    })
                tables.append({"name": table_name, "columns": columns})
        except Exception as e:
            logger.error("Failed to fetch schema for SQLite connection %s: %s", path, e)
        finally:
            await conn.close()

        return {"tables": tables}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        # Driver-level read-only guard (feature-gap #7): reject any non-SELECT
        # statement before opening a connection so a report cannot mutate data
        # through the SQLite file.
        validate_read_only_query(query)

        import aiosqlite

        path = self._path(config)
        if not path:
            raise ValueError("SQLite connection config requires a 'database'/'path' file")

        conn = await aiosqlite.connect(path)
        try:
            if parameters:
                param_values = [parameters[k] for k in sorted(parameters.keys())]
                cur = await conn.execute(query, param_values)
            else:
                cur = await conn.execute(query)
            names = [d[0] for d in cur.description] if cur.description else []
            rows = await cur.fetchall()
        finally:
            await conn.close()

        if not rows:
            return pd.DataFrame(columns=names)
        return pd.DataFrame([dict(zip(names, r)) for r in rows])
