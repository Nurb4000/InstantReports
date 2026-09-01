"""Schema service for fetching table and column metadata."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import DataConnection
from app.services.query_builder.adapter import fetch_schema
from app.services.query_builder.config import SchemaColumn, SchemaResponse, SchemaTable

logger = logging.getLogger(__name__)


class SchemaService:
    """Fetches schema information from database connections.

    Delegates the actual backend work to :func:`fetch_schema`, which dispatches
    on the connection's ``connector_type`` (PostgreSQL, MySQL, SQLite).
    """

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
        result = await db.execute(
            select(DataConnection).where(DataConnection.id == connection_id)
        )
        connection = result.scalar_one_or_none()

        if not connection:
            logger.warning(f"Connection {connection_id} not found")
            return None

        try:
            return await fetch_schema(connection.connector_type, connection.config)
        except Exception as e:
            logger.error(f"Failed to fetch schema for connection {connection_id}: {e}")
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
