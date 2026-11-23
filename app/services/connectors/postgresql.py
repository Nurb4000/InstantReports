from __future__ import annotations

import logging
from typing import Any

import asyncpg
import pandas as pd

from app.services.connectors.base import DataConnector
from app.services.connectors.read_only import validate_read_only_query
from app.services.connection_credentials import connector_secret

logger = logging.getLogger(__name__)


class PostgreSQLConnector(DataConnector):
    """Connector for PostgreSQL databases using asyncpg."""

    config_fields = [
        {"name": "host", "label": "Host", "type": "text", "default": "localhost", "required": True},
        {"name": "port", "label": "Port", "type": "number", "default": 5432, "required": False},
        {"name": "database", "label": "Database", "type": "text", "default": "", "required": True},
        {"name": "user", "label": "Username", "type": "text", "default": "", "required": True},
        {"name": "password", "label": "Password", "type": "password", "default": "", "required": True},
        {"name": "schema", "label": "Schema", "type": "text", "default": "public", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            conn = await asyncpg.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 5432),
                database=config.get("database", ""),
                user=config.get("user", ""),
                password=connector_secret(config, "password"),
                timeout=5,
            )
            await conn.execute("SELECT 1")
            await conn.close()
            return True
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        tables = []
        try:
            conn = await asyncpg.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 5432),
                database=config.get("database", ""),
                user=config.get("user", ""),
                password=connector_secret(config, "password"),
            )

            rows = await conn.fetch(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1
                ORDER BY table_name, ordinal_position
                """,
                config.get("schema", "public"),
            )

            current_table = None
            for row in rows:
                if row["table_name"] != current_table:
                    current_table = row["table_name"]
                    tables.append({
                        "name": current_table,
                        "columns": [],
                    })
                tables[-1]["columns"].append({
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                })

            await conn.close()
        except Exception as e:
            logger.error("Failed to fetch schema for PostgreSQL connection: %s", e)

        return {"tables": tables}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        # Driver-level read-only guard (feature-gap #7): reject any non-SELECT
        # statement before opening a connection so a report cannot mutate data.
        validate_read_only_query(query)
        conn = await asyncpg.connect(
            host=config.get("host", "localhost"),
            port=config.get("port", 5432),
            database=config.get("database", ""),
            user=config.get("user", ""),
            password=connector_secret(config, "password"),
        )

        try:
            if parameters:
                param_values = [parameters[k] for k in sorted(parameters.keys())]
                rows = await conn.fetch(query, *param_values)
            else:
                rows = await conn.fetch(query)

            if not rows:
                return pd.DataFrame()

            return pd.DataFrame([dict(row) for row in rows])
        finally:
            await conn.close()
