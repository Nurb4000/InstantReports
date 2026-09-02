"""Serialization helpers for exporting and importing query templates.

Templates live as JSONB rows in ``query_templates``. To share them between
users (or across deployments) we serialize a portable bundle capturing the
query configuration plus metadata. Importing reconstructs plain dicts that
the import endpoint binds to a connection and persists as new rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from app.services.query_builder.config import QueryConfig

EXPORT_VERSION = "1.0"


def _to_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _coerce_config(raw: Any) -> dict:
    """Normalize a stored/loaded query_config into a plain dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise ValueError(f"unsupported query_config type: {type(raw)!r}")


def template_to_dict(template: Any) -> dict:
    """Serialize a ``QueryTemplate`` ORM instance to a plain dict."""
    return {
        "name": template.name,
        "description": template.description,
        "connection_id": str(template.connection_id) if template.connection_id else None,
        "query_config": _coerce_config(template.query_config),
        "created_at": _to_iso(template.created_at),
        "updated_at": _to_iso(template.updated_at),
    }


def export_templates(templates: list[Any]) -> dict:
    """Wrap one or more templates in a portable export bundle."""
    return {
        "version": EXPORT_VERSION,
        "templates": [template_to_dict(t) for t in templates],
    }


def parse_import_payload(data: Any) -> list[dict]:
    """Validate an import payload and return a list of template dicts.

    Accepts either a full export bundle (``{"version": ..., "templates": [...]}``)
    or a single template dict. Each entry must contain a ``query_config`` that
    parses cleanly into a :class:`QueryConfig`.

    Raises:
        ValueError: if the payload is malformed.
    """
    if not isinstance(data, dict):
        raise ValueError("import payload must be a JSON object")

    items = data.get("templates")
    if items is None:
        items = [data]
    if not isinstance(items, list):
        raise ValueError("'templates' must be a list")

    parsed: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each template must be an object")

        raw_config = item.get("query_config")
        if raw_config is None:
            raise ValueError("template is missing 'query_config'")

        config_dict = _coerce_config(raw_config)
        # Fail fast on structurally invalid configurations.
        QueryConfig.model_validate(config_dict)

        connection_id: uuid.UUID | None = None
        raw_conn = item.get("connection_id")
        if raw_conn:
            try:
                connection_id = uuid.UUID(str(raw_conn))
            except (ValueError, TypeError):
                raise ValueError(f"invalid connection_id: {raw_conn!r}")

        name = (item.get("name") or "").strip()
        parsed.append(
            {
                "name": name or "Imported Template",
                "description": item.get("description"),
                "connection_id": connection_id,
                "query_config": config_dict,
            }
        )

    return parsed
