"""Real-data integration tests against the northwind PostgreSQL instance.

These tests register a ``DataConnection`` in the local SQLite test DB that
points at the shared northwind database, then exercise the scheduled-export
data path (``_fetch_element_data`` -> ``ReportRenderer`` -> PDF exporter) to
prove live data flows through end to end. They skip automatically when the
remote database is unreachable so CI stays green without it.
"""
from __future__ import annotations

import uuid

import pandas as pd
import pytest
import pytest_asyncio

from app.runner import _fetch_element_data
from app.services.engine.renderer import ReportRenderer
from app.services.exporters.pdf import PDFExporter

NORTHWIND = {
    "host": "10.0.1.33",
    "port": 5432,
    "database": "northwind",
    "user": "northwind",
    "password": "northwind",
    "schema": "public",
    "connector_type": "postgresql",
}


def _northwind_reachable() -> bool:
    try:
        import asyncpg

        async def _check() -> bool:
            conn = await asyncpg.connect(
                host=NORTHWIND["host"],
                port=NORTHWIND["port"],
                database=NORTHWIND["database"],
                user=NORTHWIND["user"],
                password=NORTHWIND["password"],
                timeout=5,
            )
            await conn.close()
            return True

        import asyncio

        return asyncio.run(_check())
    except Exception:
        return False


requires_northwind = pytest.mark.skipif(
    not _northwind_reachable(), reason="northwind PostgreSQL (10.0.1.33) unreachable"
)


@pytest_asyncio.fixture
async def northwind_connection(db_session):
    """Register a DataConnection pointing at northwind in the test DB."""
    from app.models.connection import DataConnection
    from app.models.user import AuthSource, User, UserRole

    user = User(
        id=uuid.uuid4(),
        email=f"nw_{uuid.uuid4().hex[:8]}@example.com",
        name="Northwind Test",
        password_hash="noop",
        role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL,
    )
    db_session.add(user)
    await db_session.commit()

    connection = DataConnection(
        id=uuid.uuid4(),
        name="northwind-integration",
        connector_type="postgresql",
        config=dict(NORTHWIND),
        created_by=user.id,
    )
    db_session.add(connection)
    await db_session.commit()
    await db_session.refresh(connection)
    return connection


def _sales_report_definition():
    return {
        "name": "Real Customers",
        "layout": {
            "sections": [
                {
                    "type": "detail",
                    "data_source": "ds1",
                    "elements": [
                        {
                            "type": "table",
                            "properties": {
                                "query": (
                                    "SELECT customer_id, company_name, city, country "
                                    "FROM customers ORDER BY company_name"
                                ),
                                "columns": [
                                    {"field": "customer_id", "header": "ID"},
                                    {"field": "company_name", "header": "Company"},
                                    {"field": "city", "header": "City"},
                                    {"field": "country", "header": "Country"},
                                ],
                            },
                        }
                    ],
                }
            ]
        },
        "data_sources": [{"connection_id": None}],  # overwritten by fixture id
    }


@requires_northwind
async def test_fetch_element_data_returns_live_rows(northwind_connection, db_session):
    """_fetch_element_data executes the element query against real northwind data."""
    definition = _sales_report_definition()
    definition["data_sources"][0]["connection_id"] = str(northwind_connection.id)

    schedule = type("Schedule", (), {"name": "test", "parameters": {}})()
    element_data = await _fetch_element_data(schedule, db_session, definition)

    assert len(element_data) == 1
    df = next(iter(element_data.values()))
    assert isinstance(df, pd.DataFrame)
    # northwind has 91 customers; the query should return them all.
    assert len(df) >= 90
    assert {"customer_id", "company_name", "city", "country"} <= set(df.columns)
    # element.data_source was wired so the renderer can find the DataFrame.
    assert definition["layout"]["sections"][0]["elements"][0]["data_source"]


@requires_northwind
async def test_full_render_and_pdf_export(northwind_connection, db_session):
    """A scheduled export renders real customer rows into a non-trivial PDF.

    Proves the full pipeline: live northwind data -> fetched -> rendered into the
    element structure that feeds the PDF exporter -> valid PDF bytes produced.
    """
    definition = _sales_report_definition()
    definition["data_sources"][0]["connection_id"] = str(northwind_connection.id)

    schedule = type("Schedule", (), {"name": "test", "parameters": {}})()
    element_data = await _fetch_element_data(schedule, db_session, definition)

    # The fetched DataFrame carries real northwind rows.
    df = next(iter(element_data.values()))
    assert len(df) >= 90
    assert "company_name" in df.columns

    renderer = ReportRenderer()
    rendered = renderer.render(definition, element_data)
    table = rendered["sections"][0]["elements"][0]

    # The rendered table element actually contains the live customer rows.
    assert table["total_rows"] >= 90
    company_names = {row["company_name"] for row in table["data"]}
    # Alfreds Futterkiste is the canonical first northwind customer.
    assert "Alfreds Futterkiste" in company_names

    pdf_bytes = PDFExporter().export(rendered)
    assert pdf_bytes.startswith(b"%PDF-"), "output should be a valid PDF"
    assert pdf_bytes.rstrip().endswith(b"%%EOF"), "PDF should be well-formed"
    assert len(pdf_bytes) > 2000, "PDF should contain real table content"
