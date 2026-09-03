from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class DataConnector(ABC):
    """Base protocol for all data connectors."""

    @abstractmethod
    async def test_connection(self, config: dict[str, Any]) -> bool:
        """Test if the connection is valid."""
        ...

    @abstractmethod
    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return schema information (tables, columns, types)."""
        ...

    @abstractmethod
    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Execute a query and return results as a DataFrame."""
        ...

    @classmethod
    def get_connector_type(cls) -> str:
        """Return the connector type string (e.g., 'postgresql')."""
        return cls.__name__.lower()


class ConnectorFactory:
    """Factory for creating connector instances based on type."""

    _connectors: dict[str, type[DataConnector]] = {}

    @classmethod
    def register(cls, connector_type: str, connector_class: type[DataConnector]) -> None:
        cls._connectors[connector_type] = connector_class

    @classmethod
    def get_connector(cls, connector_type: str) -> DataConnector:
        if connector_type not in cls._connectors:
            raise ValueError(f"Unknown connector type: {connector_type}")
        return cls._connectors[connector_type]()

    @classmethod
    def list_connectors(cls) -> list[str]:
        return list(cls._connectors.keys())


def get_connector(connector_type: str) -> DataConnector:
    """Convenience function to get a connector instance."""
    return ConnectorFactory.get_connector(connector_type)


async def resolve_data_source_connector(db, definition: dict) -> tuple[DataConnector | None, dict | None]:
    """Resolve the ``(connector, config)`` for a report's primary data source.

    Resolution order:
      1. The connection referenced by the first ``data_source`` in ``definition``.
      2. Otherwise the first PostgreSQL connection in the database.

    Returns ``(None, None)`` when no connection is available. The connector is
    always selected from the ``DataConnection.connector_type`` model column, so
    non-PostgreSQL sources resolve to the correct connector regardless of what
    lives inside the config dict. Shared by the preview route and the runner so
    the two never drift apart.
    """
    # Lazy imports avoid a circular dependency: app.models -> app.database ->
    # app.config, none of which import the connectors package.
    from sqlalchemy import select

    from app.models.connection import DataConnection

    data_sources = definition.get("data_sources") or []
    connection = None

    if data_sources:
        conn_id = data_sources[0].get("connection_id")
        if conn_id and db is not None:
            result = await db.execute(select(DataConnection).where(DataConnection.id == conn_id))
            connection = result.scalar_one_or_none()

    if connection is None and db is not None:
        result = await db.execute(
            select(DataConnection).where(DataConnection.connector_type == "postgresql").limit(1)
        )
        connection = result.scalar_one_or_none()

    if connection is None:
        return None, None

    return get_connector(connection.connector_type), connection.config
