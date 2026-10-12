from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.services.connectors.base import DataConnector

logger = logging.getLogger(__name__)


def _resolve_file_path(file_path: str) -> Path:
    """Resolve a user-supplied file path against the configured data directory.

    * If ``file_path`` is absolute, it is used directly (convenient for tests
      and deployments that mount data volumes at arbitrary paths).
    * If relative, it is resolved against ``DATA_DIR`` and must not escape it
      via ``..`` segments.
    """
    root = settings.DATA_DIR
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    raw = Path(file_path)
    if raw.is_absolute():
        return raw.resolve()

    resolved = (root / file_path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError(
            f"File path escapes data directory: {file_path!r} "
            f"(resolved to {resolved}, root is {root})"
        )
    return resolved


class CSVConnector(DataConnector):
    """Connector for CSV files."""

    config_fields = [
        {"name": "file_path", "label": "File Path", "type": "text", "default": "", "required": True},
        {"name": "delimiter", "label": "Delimiter", "type": "select", "options": [",", ";", "\t", "|"], "default": ",", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            path = _resolve_file_path(config.get("file_path", ""))
            df = await asyncio.to_thread(pd.read_csv, str(path), nrows=5)
            return not df.empty
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        file_path = config.get("file_path", "")
        try:
            path = _resolve_file_path(file_path)
            df = await asyncio.to_thread(pd.read_csv, str(path), nrows=0)
            columns = []
            for col in df.columns:
                columns.append({
                    "name": str(col),
                    "type": str(df[col].dtype),
                    "nullable": True,
                })
            # Name the table after the file (minus extension) so the field explorer
            # shows e.g. "customers" rather than a generic "csv".
            table_name = os.path.splitext(os.path.basename(file_path))[0] or "csv"
            return {"tables": [{"name": table_name, "columns": columns}]}
        except Exception:
            return {"tables": []}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        file_path = config.get("file_path", "")
        path = _resolve_file_path(file_path)
        df = await asyncio.to_thread(pd.read_csv, str(path))

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
            path = _resolve_file_path(config.get("file_path", ""))
            df = await asyncio.to_thread(pd.read_excel, str(path), nrows=5)
            return not df.empty
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            path = _resolve_file_path(config.get("file_path", ""))
            xl = await asyncio.to_thread(pd.ExcelFile, str(path))
            tables = []
            for sheet in xl.sheet_names:
                df = await asyncio.to_thread(pd.read_excel, str(path), sheet_name=sheet, nrows=0)
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
        path = _resolve_file_path(config.get("file_path", ""))
        df = await asyncio.to_thread(pd.read_excel, str(path), sheet_name=sheet_name)

        if query:
            try:
                import pandasql as psql

                df = psql.sqldf(query, locals())
            except ImportError:
                logger.warning(
                    "pandasql not installed; ignoring SQL query for Excel connector. "
                    "Parameters are still applied below."
                )

        if parameters:
            for key, value in parameters.items():
                col = key.replace("$", "")
                if col in df.columns and value is not None:
                    df = df[df[col] == value]

        return df
