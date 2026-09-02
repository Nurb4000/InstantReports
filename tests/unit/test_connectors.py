"""Unit tests for the connector registry and config-field metadata.

These tests exercise the dependency-free surface of the connector subsystem:
the ConnectorFactory registry and each connector's ``config_fields`` metadata,
which drives the dynamic connection forms in the UI. Actual driver-dependent
methods (test_connection/get_schema/execute_query) require external DB drivers
that are not installed in the test environment and are covered elsewhere.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.connectors.base import ConnectorFactory, DataConnector
from app.services.query_builder.config import SchemaColumn, SchemaResponse, SchemaTable

ALL_CONNECTOR_TYPES = {
    "postgresql",
    "mysql",
    "sqlserver",
    "odbc",
    "csv",
    "excel",
    "api",
    "graphql",
}


def _field_names(connector_type: str) -> set[str]:
    connector = ConnectorFactory.get_connector(connector_type)
    return {f["name"] for f in connector.config_fields}


# --------------------------------------------------------------------------- #
# ConnectorFactory registry
# --------------------------------------------------------------------------- #


def test_all_connectors_registered():
    registered = set(ConnectorFactory.list_connectors())
    assert ALL_CONNECTOR_TYPES <= registered


def test_get_connector_returns_instance():
    connector = ConnectorFactory.get_connector("postgresql")
    assert isinstance(connector, DataConnector)


def test_get_connector_unknown_raises():
    with pytest.raises(ValueError):
        ConnectorFactory.get_connector("does-not-exist")


def test_get_connector_returns_fresh_instance():
    a = ConnectorFactory.get_connector("mysql")
    b = ConnectorFactory.get_connector("mysql")
    assert a is not b


def test_get_connector_type_lowercase_class_name():
    connector = ConnectorFactory.get_connector("postgresql")
    assert connector.get_connector_type() == "postgresqlconnector"


# --------------------------------------------------------------------------- #
# config_fields structure
# --------------------------------------------------------------------------- #


def test_every_field_has_required_keys():
    for connector_type in ALL_CONNECTOR_TYPES:
        connector = ConnectorFactory.get_connector(connector_type)
        assert connector.config_fields, f"{connector_type} has no config fields"
        for field in connector.config_fields:
            assert "name" in field and "label" in field and "type" in field
            assert isinstance(field.get("required"), bool)


def test_postgresql_fields_and_defaults():
    fields = {f["name"]: f for f in ConnectorFactory.get_connector("postgresql").config_fields}
    assert set(fields) == {"host", "port", "database", "user", "password", "schema"}
    assert fields["host"]["default"] == "localhost"
    assert fields["port"]["default"] == 5432
    assert fields["schema"]["default"] == "public"
    assert fields["database"]["required"] is True
    assert fields["port"]["required"] is False


def test_mysql_fields():
    assert _field_names("mysql") == {"host", "port", "database", "user", "password"}


def test_sqlserver_fields():
    assert _field_names("sqlserver") == {"host", "port", "database", "user", "password", "schema"}


def test_odbc_fields():
    assert _field_names("odbc") == {"dsn", "user", "password"}


def test_csv_fields():
    fields = {f["name"]: f for f in ConnectorFactory.get_connector("csv").config_fields}
    assert set(fields) == {"file_path", "delimiter"}
    assert fields["delimiter"]["default"] == ","


def test_excel_fields():
    assert _field_names("excel") == {"file_path", "sheet"}


def test_api_fields_include_auth_and_method():
    fields = {f["name"]: f for f in ConnectorFactory.get_connector("api").config_fields}
    assert {"url", "method", "auth_type"} <= set(fields)
    assert fields["method"]["default"] == "GET"


def test_graphql_fields():
    assert _field_names("graphql") == {"url", "headers"}


# --------------------------------------------------------------------------- #
# Mock demo schema (used when a DB is unavailable for dev/testing)
# --------------------------------------------------------------------------- #


def test_mock_schema_returns_valid_response():
    from app.services.query_builder.schema import SchemaService

    response = asyncio.run(SchemaService._get_mock_schema("some-connection-id"))
    assert isinstance(response, SchemaResponse)
    assert response.connection_name == "Northwind Demo (Mock)"
    assert len(response.tables) == 5


def test_mock_schema_tables_and_foreign_keys():
    from app.services.query_builder.schema import SchemaService

    response = asyncio.run(SchemaService._get_mock_schema("some-connection-id"))
    table_names = {t.name for t in response.tables}
    assert table_names == {"employees", "orders", "order_details", "products", "categories"}

    orders = next(t for t in response.tables if t.name == "orders")
    fk_columns = {c.name: c for c in orders.columns if c.is_foreign_key}
    assert "employee_id" in fk_columns
    assert fk_columns["employee_id"].foreign_key_table == "employees"
    assert fk_columns["customer_id"].foreign_key_table == "customers"


def test_mock_schema_primary_keys_marked():
    from app.services.query_builder.schema import SchemaService

    response = asyncio.run(SchemaService._get_mock_schema("some-connection-id"))
    employees = next(t for t in response.tables if t.name == "employees")
    emp_id = next(c for c in employees.columns if c.name == "employee_id")
    assert emp_id.is_primary_key is True
    assert emp_id.nullable is False


def test_schema_models_roundtrip():
    table = SchemaTable(name="t", columns=[SchemaColumn(name="id", data_type="int")])
    response = SchemaResponse(tables=[table], connection_name="c")
    assert response.tables[0].name == "t"
    assert response.tables[0].columns[0].name == "id"
