"""Schema service for fetching table and column metadata."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import DataConnection
from app.services.query_builder.config import SchemaColumn, SchemaResponse, SchemaTable

logger = logging.getLogger(__name__)


class SchemaService:
    """Fetches schema information from database connections."""

    @staticmethod
    async def get_schema(
        db: AsyncSession, connection_id: str
    ) -> Optional[SchemaResponse]:
        """Get schema information for a database connection.

        Args:
            db: Database session
            connection_id: UUID of the data connection

        Returns:
            SchemaResponse with tables and columns, or None if connection not found
        """
        # Get connection config
        result = await db.execute(
            text("SELECT config FROM data_connections WHERE id = :id"),
            {"id": connection_id},
        )
        row = result.fetchone()

        if not row:
            logger.warning(f"Connection {connection_id} not found")
            return None

        config = row[0] if isinstance(row[0], dict) else eval(row[0])

        # Extract connection parameters
        host = config.get("host", "localhost")
        port = int(config.get("port", 5432))
        user = config.get("user", "")
        password = config.get("password", "")
        database = config.get("database", "")
        schema_name = config.get("schema", "public")

        try:
            # Connect to the actual database using asyncpg
            conn = await asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
            )

            # Fetch all tables in the schema
            tables_query = """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = $1 AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """
            tables = await conn.fetch(tables_query, schema_name)

            schema_tables = []
            for table_row in tables:
                table_name = table_row[0]
                
                # Fetch columns for this table
                columns_query = """
                    SELECT 
                        column_name,
                        data_type,
                        is_nullable,
                        column_default,
                        character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = $1 AND table_name = $2
                    ORDER BY ordinal_position
                """
                columns = await conn.fetch(columns_query, schema_name, table_name)
                
                # Fetch primary key information
                pk_query = """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    WHERE tc.table_schema = $1 
                        AND tc.table_name = $2
                        AND tc.constraint_type = 'PRIMARY KEY'
                """
                pk_result = await conn.fetch(pk_query, schema_name, table_name)
                pk_columns = [row[0] for row in pk_result] if pk_result else []

                # Fetch foreign key information
                fk_query = """
                    SELECT 
                        kcu.column_name,
                        ccu.table_name AS referenced_table,
                        ccu.column_name AS referenced_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                        AND tc.table_schema = ccu.table_schema
                    WHERE tc.table_schema = $1 
                        AND tc.table_name = $2
                        AND tc.constraint_type = 'FOREIGN KEY'
                """
                fk_result = await conn.fetch(fk_query, schema_name, table_name)
                fk_map = {}
                for row in fk_result:
                    fk_map[row[0]] = {
                        "table": row[1],
                        "column": row[2]
                    }

                # Build columns list
                table_columns = []
                for col in columns:
                    col_name = col[0]
                    data_type = col[1]
                    is_nullable = col[2] == "YES"
                    
                    # Simplify data types
                    if "character_varying" in data_type or "varchar" in data_type:
                        data_type = "VARCHAR"
                    elif "numeric" in data_type or "decimal" in data_type:
                        data_type = "DECIMAL"
                    elif "integer" in data_type or "int" in data_type:
                        data_type = "INTEGER"
                    elif "boolean" in data_type or "bool" in data_type:
                        data_type = "BOOLEAN"
                    elif "timestamp" in data_type:
                        data_type = "TIMESTAMP"
                    elif "date" in data_type:
                        data_type = "DATE"

                    is_pk = col_name in pk_columns
                    fk_info = fk_map.get(col_name)
                    
                    table_columns.append(SchemaColumn(
                        name=col_name,
                        data_type=data_type,
                        nullable=is_nullable,
                        is_primary_key=is_pk,
                        is_foreign_key=fk_info is not None,
                        foreign_key_table=fk_info["table"] if fk_info else None,
                        foreign_key_column=fk_info["column"] if fk_info else None,
                    ))

                schema_tables.append(SchemaTable(
                    name=table_name,
                    columns=table_columns,
                ))

            await conn.close()

            return SchemaResponse(
                tables=schema_tables,
                connection_name=config.get("name", "Database"),
            )

        except Exception as e:
            logger.error(f"Failed to fetch schema from database: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return mock schema on error
            return await SchemaService._get_mock_schema(connection_id)

    @staticmethod
    async def _get_mock_schema(connection_id: str) -> SchemaResponse:
        """Return mock schema for development/testing when database is unavailable."""
        # Mock Northwind schema
        tables = [
            SchemaTable(
                name="employees",
                columns=[
                    SchemaColumn(
                        name="employee_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_primary_key=True,
                    ),
                    SchemaColumn(
                        name="first_name",
                        data_type="VARCHAR",
                        nullable=False,
                    ),
                    SchemaColumn(
                        name="last_name",
                        data_type="VARCHAR",
                        nullable=False,
                    ),
                    SchemaColumn(
                        name="title",
                        data_type="VARCHAR",
                        nullable=True,
                    ),
                    SchemaColumn(
                        name="city",
                        data_type="VARCHAR",
                        nullable=True,
                    ),
                ],
            ),
            SchemaTable(
                name="orders",
                columns=[
                    SchemaColumn(
                        name="order_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_primary_key=True,
                    ),
                    SchemaColumn(
                        name="employee_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_foreign_key=True,
                        foreign_key_table="employees",
                        foreign_key_column="employee_id",
                    ),
                    SchemaColumn(
                        name="customer_id",
                        data_type="INTEGER",
                        nullable=True,
                        is_foreign_key=True,
                        foreign_key_table="customers",
                        foreign_key_column="customer_id",
                    ),
                    SchemaColumn(
                        name="order_date",
                        data_type="DATE",
                        nullable=True,
                    ),
                ],
            ),
            SchemaTable(
                name="order_details",
                columns=[
                    SchemaColumn(
                        name="order_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_foreign_key=True,
                        foreign_key_table="orders",
                        foreign_key_column="order_id",
                    ),
                    SchemaColumn(
                        name="product_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_foreign_key=True,
                        foreign_key_table="products",
                        foreign_key_column="product_id",
                    ),
                    SchemaColumn(
                        name="unit_price",
                        data_type="DECIMAL",
                        nullable=False,
                    ),
                    SchemaColumn(
                        name="quantity",
                        data_type="INTEGER",
                        nullable=False,
                    ),
                ],
            ),
            SchemaTable(
                name="products",
                columns=[
                    SchemaColumn(
                        name="product_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_primary_key=True,
                    ),
                    SchemaColumn(
                        name="product_name",
                        data_type="VARCHAR",
                        nullable=False,
                    ),
                    SchemaColumn(
                        name="category_id",
                        data_type="INTEGER",
                        nullable=True,
                        is_foreign_key=True,
                        foreign_key_table="categories",
                        foreign_key_column="category_id",
                    ),
                ],
            ),
            SchemaTable(
                name="categories",
                columns=[
                    SchemaColumn(
                        name="category_id",
                        data_type="INTEGER",
                        nullable=False,
                        is_primary_key=True,
                    ),
                    SchemaColumn(
                        name="category_name",
                        data_type="VARCHAR",
                        nullable=False,
                    ),
                ],
            ),
        ]

        return SchemaResponse(
            tables=tables,
            connection_name="Northwind Demo (Mock)",
        )


# Convenience function
async def get_schema(db: AsyncSession, connection_id: str) -> Optional[SchemaResponse]:
    """Get schema for a database connection."""
    return await SchemaService.get_schema(db, connection_id)
