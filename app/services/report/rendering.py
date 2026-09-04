"""Render a report definition against live data and export to bytes.

This module is intentionally free of delivery/transport imports so route
handlers (portal, designer) can use it without pulling in the optional
dependencies the runner's delivery stack requires (asyncssh, smbclient,
aiosmtplib). ``app.runner`` delegates its scheduled-data fetching to
:func:`fetch_element_data` to avoid duplicating the query-execution logic.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.models.connection import DataConnection
from app.services.connectors.base import get_connector
from app.services.engine.data_processor import DataProcessor
from app.services.engine.renderer import ReportRenderer
from app.services.exporters import export_report, normalize_output_format

logger = logging.getLogger(__name__)


async def fetch_element_data(
    definition: dict[str, Any],
    db,
    parameters: dict[str, Any] | None = None,
    label: str = "report",
) -> dict[str, pd.DataFrame]:
    """Execute each data-bearing element's query against its connection.

    Exports must render against *live* data at run time, so every table/chart
    element that carries a ``properties.query`` is executed against the report's
    primary connection and the resulting DataFrame is keyed into a ``data`` dict
    that ``ReportRenderer.render`` consumes. Element ``data_source`` ids are set
    to match the keys so the renderer can locate them (the designer does not
    persist a per-element data_source).
    """
    data_sources = definition.get("data_sources") or []
    connections: dict[uuid.UUID, DataConnection] = {}
    for ds in data_sources:
        raw_id = ds.get("connection_id")
        if not raw_id:
            continue
        # connection_id is stored as a string after JSON round-trip; coerce to a
        # UUID so the query works against the typed column on every dialect.
        try:
            conn_id = uuid.UUID(str(raw_id))
        except (ValueError, AttributeError, TypeError):
            logger.warning("Invalid connection_id '%s' in %s", raw_id, label)
            continue
        result = await db.execute(select(DataConnection).where(DataConnection.id == conn_id))
        conn = result.scalar_one_or_none()
        if conn:
            connections[conn_id] = conn

    if not connections:
        logger.warning("No data connections found for %s — report will render empty", label)
        return {}

    primary = next(iter(connections.values()))
    try:
        connector = get_connector(primary.connector_type)
    except Exception as exc:
        logger.error("Could not load connector '%s': %s", primary.connector_type, exc)
        return {}

    element_data: dict[str, pd.DataFrame] = {}
    sections = definition.get("layout", {}).get("sections", [])
    for section in sections:
        for element in section.get("elements", []):
            if element.get("type") not in ("table", "chart"):
                continue
            query = (element.get("properties") or {}).get("query")
            if not query:
                continue
            try:
                df = await connector.execute_query(primary.config, query, parameters)
                # Apply report-level calculated fields / grouping for table
                # elements so the scheduled and on-demand export paths match the
                # live-preview path (preview.py calls DataProcessor.process there).
                # Without this the calculated_fields stored in the definition are
                # silently dropped from every exported report.
                if df is not None and not df.empty and element.get("type") == "table":
                    df = DataProcessor().process(df, definition)
                key = f"ds_{len(element_data)}"
                element["data_source"] = key
                element_data[key] = df if df is not None else pd.DataFrame()
            except Exception as exc:
                logger.error(
                    "Failed to execute query for %s in %s: %s",
                    element.get("type"),
                    label,
                    exc,
                )

    return element_data


def render_report_bytes(
    definition: dict[str, Any],
    element_data: dict[str, pd.DataFrame],
    output_format: str = "pdf",
) -> bytes:
    """Render a definition and export it to bytes for the given format.

    ``output_format`` is normalized (accepting common aliases); an unknown value
    raises ``ValueError`` so callers can surface a clear 400.
    """
    fmt = normalize_output_format(output_format)
    rendered = ReportRenderer().render(definition, element_data)
    return export_report(rendered, fmt)
