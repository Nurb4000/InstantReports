from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.services.connectors.base import DataConnector

logger = logging.getLogger(__name__)


class SQLServerConnector(DataConnector):
    """Connector for Microsoft SQL Server using pymssql."""

    config_fields = [
        {"name": "host", "label": "Server", "type": "text", "default": "localhost", "required": True},
        {"name": "port", "label": "Port", "type": "number", "default": 1433, "required": False},
        {"name": "database", "label": "Database", "type": "text", "default": "", "required": True},
        {"name": "user", "label": "Username", "type": "text", "default": "", "required": True},
        {"name": "password", "label": "Password", "type": "password", "default": "", "required": True},
        {"name": "schema", "label": "Schema", "type": "text", "default": "dbo", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            import pymssql
            conn = pymssql.connect(
                server=config.get("host", "localhost"),
                port=config.get("port", 1433),
                user=config.get("user", ""),
                password=config.get("password", ""),
                database=config.get("database", ""),
                timeout=5,
            )
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            conn.close()
            return True
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        tables = []
        try:
            import pymssql
            conn = pymssql.connect(
                server=config.get("host", "localhost"),
                port=config.get("port", 1433),
                user=config.get("user", ""),
                password=config.get("password", ""),
                database=config.get("database", ""),
            )
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = @schema
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                {"@schema": config.get("schema", "dbo")},
            )

            current_table = None
            for row in cursor.fetchall():
                if row[0] != current_table:
                    current_table = row[0]
                    tables.append({"name": current_table, "columns": []})
                tables[-1]["columns"].append({
                    "name": row[1],
                    "type": row[2],
                    "nullable": row[3] == "YES",
                })

            cursor.close()
            conn.close()
        except Exception as e:
            logger.error("Failed to fetch schema for SQL Server connection: %s", e)

        return {"tables": tables}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        import pymssql

        conn = pymssql.connect(
            server=config.get("host", "localhost"),
            port=config.get("port", 1433),
            user=config.get("user", ""),
            password=config.get("password", ""),
            database=config.get("database", ""),
        )

        try:
            cursor = conn.cursor()
            if parameters:
                param_values = tuple(parameters[k] for k in sorted(parameters.keys()))
                cursor.execute(query, param_values)
            else:
                cursor.execute(query)

            result = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            if not result:
                return pd.DataFrame()

            return pd.DataFrame(result, columns=columns)
        finally:
            cursor.close()
            conn.close()
