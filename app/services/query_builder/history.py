"""Query history service for the SQL Query Builder.

Tracks snapshots of a query configuration so a table/chart element's query can be
inspected and restored over time. Mirrors the report-versioning pattern but scoped
to an individual builder query (identified by report_id + element_id) rather than a
whole report definition.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import QueryHistory


async def save_snapshot(
    db: AsyncSession,
    query_config: dict,
    *,
    report_id: uuid.UUID | None = None,
    element_id: str | None = None,
    connection_id: uuid.UUID | None = None,
    label: str | None = None,
    user_id: uuid.UUID | None = None,
) -> QueryHistory:
    """Persist a new query-history snapshot."""
    snapshot = QueryHistory(
        report_id=report_id,
        element_id=element_id,
        connection_id=connection_id,
        query_config=query_config,
        label=label,
        created_by=user_id,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


async def list_snapshots(
    db: AsyncSession,
    *,
    report_id: uuid.UUID | None = None,
    element_id: str | None = None,
    connection_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[QueryHistory]:
    """List snapshots ordered newest-first, filtered by the provided keys."""
    query = select(QueryHistory)

    if report_id is not None:
        query = query.where(QueryHistory.report_id == report_id)
    if element_id is not None:
        query = query.where(QueryHistory.element_id == element_id)
    if connection_id is not None:
        query = query.where(QueryHistory.connection_id == connection_id)

    query = query.order_by(QueryHistory.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_snapshot(db: AsyncSession, snapshot_id: uuid.UUID) -> QueryHistory | None:
    """Fetch a single snapshot by id."""
    return await db.get(QueryHistory, snapshot_id)


async def delete_snapshot(db: AsyncSession, snapshot_id: uuid.UUID) -> bool:
    """Delete a single snapshot. Returns True if something was removed."""
    snapshot = await db.get(QueryHistory, snapshot_id)
    if not snapshot:
        return False
    await db.delete(snapshot)
    await db.commit()
    return True
