from __future__ import annotations

import asyncio
import io
from typing import Any

import pandas as pd

from app.services.connectors.base import DataConnector


class CSVConnector(DataConnector):
    """Connector for CSV files."""

    config_fields = [
        {"name": "file_path", "label": "File Path", "type": "text", "default": "", "required": True},
        {"name": "delimiter", "label": "Delimiter", "type": "select", "options": [",", ";", "\t", "|"], "default": ",", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            df = await asyncio.to_thread(pd.read_csv, config.get("file_path", ""), nrows=5)
            return not df.empty
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            df = await asyncio.to_thread(pd.read_csv, config.get("file_path", ""), nrows=0)
            columns = []
            for col in df.columns:
                columns.append({
                    "name": str(col),
                    "type": str(df[col].dtype),
                    "nullable": True,
                })
            return {"tables": [{"name": config.get("file_name", "csv"), "columns": columns}]}
        except Exception:
            return {"tables": []}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        file_path = config.get("file_path", "")
        df = await asyncio.to_thread(pd.read_csv, file_path)

        if parameters:
            for key, value in parameters.items():
                col = key.replace("$", "")
                if col in df.columns:
                    df = df[df[col] == value]

        return df


class ExcelConnector(DataConnector):
    """Connector for Excel files (.xlsx, .xls)."""

    config_fields = [
        {"name": "file_path", "label": "File Path", "type": "text", "default": "", "required": True},
        {"name": "sheet", "label": "Sheet Name/Index", "type": "text", "default": "0", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            df = await asyncio.to_thread(pd.read_excel, config.get("file_path", ""), nrows=5)
            return not df.empty
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            xl = await asyncio.to_thread(pd.ExcelFile, config.get("file_path", ""))
            tables = []
            for sheet in xl.sheet_names:
                df = await asyncio.to_thread(pd.read_excel, config.get("file_path", ""), sheet_name=sheet, nrows=0)
                columns = []
                for col in df.columns:
                    columns.append({
                        "name": str(col),
                        "type": str(df[col].dtype),
                        "nullable": True,
                    })
                tables.append({"name": sheet, "columns": columns})
            xl.close()
            return {"tables": tables}
        except Exception:
            return {"tables": []}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        sheet_name = config.get("sheet", 0)
        df = await asyncio.to_thread(pd.read_excel, config.get("file_path", ""), sheet_name=sheet_name)

        if parameters and query:
            try:
                import pandasql as psql
                df = psql.sqldf(query, locals())
            except ImportError:
                pass

        return df
