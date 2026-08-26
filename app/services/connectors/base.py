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
