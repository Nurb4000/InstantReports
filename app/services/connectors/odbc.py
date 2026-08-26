from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd


class ODBCCConnector(DataConnector):
    """Connector for ODBC data sources using aioodbc."""

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            import aioodbc
            conn = await aioodbc.connect(dsn=config.get("dsn", ""), timeout=5)
            cursor = await conn.cursor()
            await cursor.execute("SELECT 1")
            await cursor.close()
            await conn.close()
            return True
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        tables = []
        try:
            import aioodbc
            conn = await aioodbc.connect(dsn=config.get("dsn", ""))
            cursor = await conn.cursor()

            await cursor.execute("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS")
            rows = await cursor.fetchall()

            current_table = None
            for row in rows:
                if row[0] != current_table:
                    current_table = row[0]
                    tables.append({"name": current_table, "columns": []})
                tables[-1]["columns"].append({
                    "name": row[1],
                    "type": row[2],
                    "nullable": True,
                })

            await cursor.close()
            await conn.close()
        except Exception:
            pass

        return {"tables": tables}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        import aioodbc

        conn = await aioodbc.connect(dsn=config.get("dsn", ""))
        cursor = await conn.cursor()

        try:
            if parameters:
                param_values = tuple(parameters[k] for k in sorted(parameters.keys()))
                await cursor.execute(query, param_values)
            else:
                await cursor.execute(query)

            result = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            if not result:
                return pd.DataFrame()

            return pd.DataFrame(result, columns=columns)
        finally:
            await cursor.close()
            await conn.close()
