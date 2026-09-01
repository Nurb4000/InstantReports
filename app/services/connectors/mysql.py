from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from app.services.connectors.base import DataConnector


class MySQLConnector(DataConnector):
    """Connector for MySQL/MariaDB databases using asyncmy."""

    config_fields = [
        {"name": "host", "label": "Host", "type": "text", "default": "localhost", "required": True},
        {"name": "port", "label": "Port", "type": "number", "default": 3306, "required": False},
        {"name": "database", "label": "Database", "type": "text", "default": "", "required": True},
        {"name": "user", "label": "Username", "type": "text", "default": "", "required": True},
        {"name": "password", "label": "Password", "type": "password", "default": "", "required": True},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            import asyncmy
            conn = await asyncmy.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 3306),
                database=config.get("database", ""),
                user=config.get("user", ""),
                password=config.get("password", ""),
                connect_timeout=5,
            )
            await conn.execute("SELECT 1")
            await conn.close()
            return True
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        tables = []
        try:
            import asyncmy
            conn = await asyncmy.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 3306),
                database=config.get("database", ""),
                user=config.get("user", ""),
                password=config.get("password", ""),
            )

            rows = await conn.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                config.get("schema", config.get("database", "")),
            )

            current_table = None
            async for row in rows:
                if row[0] != current_table:
                    current_table = row[0]
                    tables.append({"name": current_table, "columns": []})
                tables[-1]["columns"].append({
                    "name": row[1],
                    "type": row[2],
                    "nullable": row[3] == "YES",
                })

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
        import asyncmy

        conn = await asyncmy.connect(
            host=config.get("host", "localhost"),
            port=config.get("port", 3306),
            database=config.get("database", ""),
            user=config.get("user", ""),
            password=config.get("password", ""),
        )

        try:
            if parameters:
                param_values = [parameters[k] for k in sorted(parameters.keys())]
                rows = await conn.execute(query, *param_values)
            else:
                rows = await conn.execute(query)

            result = await rows.fetchall()
            columns = [desc[0] for desc in rows.description] if rows.description else []

            if not result:
                return pd.DataFrame()

            return pd.DataFrame(result, columns=columns)
        finally:
            await conn.close()
