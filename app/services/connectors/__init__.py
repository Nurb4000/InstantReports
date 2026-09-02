from __future__ import annotations

from app.services.connectors.base import ConnectorFactory, DataConnector
from app.services.connectors.csv_excel import CSVConnector, ExcelConnector
from app.services.connectors.mysql import MySQLConnector
from app.services.connectors.odbc import ODBCCConnector
from app.services.connectors.postgresql import PostgreSQLConnector
from app.services.connectors.rest_graphql import GraphQLConnector, RESTAPIConnector
from app.services.connectors.sqlserver import SQLServerConnector

# Register all connectors with the factory
ConnectorFactory.register("postgresql", PostgreSQLConnector)
ConnectorFactory.register("mysql", MySQLConnector)
ConnectorFactory.register("sqlserver", SQLServerConnector)
ConnectorFactory.register("odbc", ODBCCConnector)
ConnectorFactory.register("csv", CSVConnector)
ConnectorFactory.register("excel", ExcelConnector)
ConnectorFactory.register("api", RESTAPIConnector)
ConnectorFactory.register("graphql", GraphQLConnector)

__all__ = [
    "CSVConnector",
    "ConnectorFactory",
    "DataConnector",
    "ExcelConnector",
    "GraphQLConnector",
    "MySQLConnector",
    "ODBCCConnector",
    "PostgreSQLConnector",
    "RESTAPIConnector",
    "SQLServerConnector",
]
