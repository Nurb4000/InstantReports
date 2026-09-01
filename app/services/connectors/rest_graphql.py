from __future__ import annotations

import json
from typing import Any

import httpx
import pandas as pd

from app.services.connectors.base import DataConnector


class RESTAPIConnector(DataConnector):
    """Connector for REST API endpoints."""

    config_fields = [
        {"name": "url", "label": "URL", "type": "text", "default": "", "required": True},
        {"name": "method", "label": "Method", "type": "select", "options": ["GET", "POST"], "default": "GET", "required": False},
        {"name": "auth_type", "label": "Auth Type", "type": "select", "options": ["none", "bearer", "basic"], "default": "none", "required": False},
        {"name": "auth_token", "label": "Bearer Token", "type": "password", "default": "", "required": False},
        {"name": "auth_username", "label": "Basic Username", "type": "text", "default": "", "required": False},
        {"name": "auth_password", "label": "Basic Password", "type": "password", "default": "", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await self._make_request(client, config, "GET")
                return response.status_code in (200, 201)
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await self._make_request(client, config, "GET")
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and len(data) > 0:
                        sample = data[0] if isinstance(data[0], dict) else {"data": data[0]}
                        columns = []
                        for key, value in sample.items():
                            columns.append({
                                "name": str(key),
                                "type": self._infer_type(value),
                                "nullable": value is None,
                            })
                        return {"tables": [{"name": config.get("endpoint", "api"), "columns": columns}]}
        except Exception:
            pass
        return {"tables": []}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._make_request(client, config, "GET", parameters)

            if response.status_code != 200:
                raise Exception(f"API request failed: {response.status_code}")

            data = response.json()

            if isinstance(data, list):
                return pd.DataFrame(data)
            elif isinstance(data, dict):
                if "data" in data and isinstance(data["data"], list):
                    return pd.DataFrame(data["data"])
                return pd.DataFrame([data])
            else:
                return pd.DataFrame()

    async def _make_request(
        self,
        client: httpx.AsyncClient,
        config: dict[str, Any],
        method: str = "GET",
        parameters: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = config.get("url", "")
        headers = config.get("headers", {})
        auth = config.get("auth", {})

        if auth.get("type") == "bearer":
            headers["Authorization"] = f"Bearer {auth.get('token', '')}"
        elif auth.get("type") == "basic":
            import base64
            credentials = base64.b64encode(
                f"{auth.get('username', '')}:{auth.get('password', '')}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"

        params = parameters or {}
        if config.get("params"):
            params.update(config["params"])

        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
        )
        return response

    @staticmethod
    def _infer_type(value: Any) -> str:
        if isinstance(value, (int, float)):
            return "number"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, dict):
            return "object"
        elif isinstance(value, list):
            return "array"
        else:
            return "string"


class GraphQLConnector(DataConnector):
    """Connector for GraphQL endpoints."""

    config_fields = [
        {"name": "url", "label": "Endpoint URL", "type": "text", "default": "", "required": True},
        {"name": "headers", "label": "Custom Headers (JSON)", "type": "textarea", "default": "", "required": False},
    ]

    async def test_connection(self, config: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await self._make_request(client, config, "{ __typename }")
                return response.status_code == 200
        except Exception:
            return False

    async def get_schema(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                introspection_query = """
                {
                    __schema {
                        queryType { name }
                        types {
                            name
                            kind
                            fields { name type { name kind } }
                        }
                    }
                }
                """
                response = await self._make_request(client, config, introspection_query)
                if response.status_code == 200:
                    data = response.json()
                    types = data.get("data", {}).get("__schema", {}).get("types", [])
                    tables = []
                    for type_def in types:
                        if type_def.get("kind") == "OBJECT" and type_def.get("name", "").startswith(("Query", "Mutation")):
                            continue
                        fields = type_def.get("fields", []) or []
                        columns = []
                        for field in fields:
                            col_type = field.get("type", {})
                            columns.append({
                                "name": field["name"],
                                "type": col_type.get("name", "String"),
                                "nullable": col_type.get("kind") == "NULLABLE" or not col_type.get("name", "").endswith("!"),
                            })
                        tables.append({
                            "name": type_def["name"],
                            "columns": columns,
                        })
                    return {"tables": tables}
        except Exception:
            pass
        return {"tables": []}

    async def execute_query(
        self,
        config: dict[str, Any],
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._make_request(client, config, query, parameters)

            if response.status_code != 200:
                raise Exception(f"GraphQL request failed: {response.status_code}")

            data = response.json()
            errors = data.get("errors")
            if errors:
                raise Exception(f"GraphQL errors: {errors}")

            result_data = data.get("data", {})
            if isinstance(result_data, dict):
                for key, value in result_data.items():
                    if isinstance(value, list):
                        return pd.DataFrame(value)
                    elif isinstance(value, dict):
                        return pd.DataFrame([value])

            return pd.DataFrame()

    async def _make_request(
        self,
        client: httpx.AsyncClient,
        config: dict[str, Any],
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = config.get("url", "")
        headers = {
            "Content-Type": "application/json",
            **(config.get("headers", {})),
        }

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        response = await client.post(url, json=payload, headers=headers)
        return response
