from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

# Config keys whose values are secrets and must never be returned verbatim in an
# API response. Matched case-insensitively so any new secret field (e.g.
# "client_secret", "auth_token") is masked even if this pattern is not updated.
_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|pwd|token|secret|api[_-]?key|access[_-]?key|"
    r"secret[_-]?key|auth(orization)?|private[_-]?key|credential)",
    re.IGNORECASE,
)

# Value-based secret detector (M8). Credentials are sometimes stored under an
# opaque key name that does not match ``_SECRET_KEY_RE`` -- e.g. a full
# connection string / DSN / URI in a field named ``connection_string``, ``dsn``,
# ``uri``, ``url`` or ``conn``. This flags such a value so it is masked in API
# responses and encrypted at rest even though the key name gives no hint. Only a
# URI that embeds an ``user:password@`` authority is treated as secret; a bare
# ``scheme://host`` (no embedded credential) is not, to avoid masking public URLs.
_CREDENTIAL_URI_RE = re.compile(
    r"^[a-zA-Z][\w+.\-]*://[^\s:/@]{1,255}:[^\s:/@]+@",
    re.IGNORECASE,
)


def _looks_like_secret_value(value: Any) -> bool:
    """Return True if ``value`` embeds credentials (M8 value-based detection).

    Complements the key-name matcher: a string like
    ``postgresql://user:PASSWORD@host/db`` carries a secret in its value even when
    stored under an opaque key. Non-strings, empty strings, and very long values
    (unlikely to be a single credential) are treated as non-secrets.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or len(stripped) > 4096:
        return False
    return bool(_CREDENTIAL_URI_RE.search(stripped))


def is_secret(config_key: str, value: Any) -> bool:
    """Return True if a config entry is secret by key name OR by its value (M8).

    Single point of truth shared by response redaction and at-rest encryption so
    the two never disagree on what must be masked/encrypted.
    """
    return bool(_SECRET_KEY_RE.search(config_key)) or _looks_like_secret_value(value)


def redact_connection_config(
    config: dict[str, Any] | None, mask: str = "***"
) -> dict[str, Any]:
    """Return a copy of ``config`` with secret values masked.

    Used when serializing a connection for an API response so plaintext
    passwords, bearer tokens, and basic-auth creds are never leaked to callers.
    A value is masked when its key matches the secret-name pattern OR when the
    value itself embeds credentials (e.g. a ``postgresql://user:pass@host`` DSN
    stored under an opaque ``connection_string`` key). Non-secret fields (host,
    port, database, user, public URLs) pass through unchanged.
    """
    if not config:
        return {}
    return {
        key: (mask if is_secret(key, value) else value)
        for key, value in config.items()
    }


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

    # Resolve credentials so a retired secret still within its grace period
    # (Gap #11) is re-exposed under ``_previous_<field>`` keys. Connectors read
    # through ``connector_secret`` and fall back to it when the current value is
    # absent, so rotation does not break in-flight or freshly scheduled reports.
    from app.services.connection_credentials import resolve_connection_config

    resolved_config = await resolve_connection_config(db, connection)
    return get_connector(connection.connector_type), resolved_config
