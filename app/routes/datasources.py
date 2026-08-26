from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_optional
from app.database import get_db
from app.models.connection import DataConnection
from app.models.user import User

router = APIRouter()


@router.get("/")
async def list_connections(
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(DataConnection).order_by(DataConnection.updated_at.desc()))
    connections = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "name": c.name,
            "connector_type": c.connector_type,
            "created_at": c.created_at.isoformat(),
        }
        for c in connections
    ]


@router.post("/")
async def create_connection(
    request: Request,
    name: str = None,
    connector_type: str = None,
    config: dict = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    connection = DataConnection(
        name=name or "Unnamed Connection",
        connector_type=connector_type or "postgresql",
        config=config or {},
        created_by=current_user.id,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    return {"status": "ok", "id": str(connection.id)}


@router.get("/{connection_id}")
async def get_connection(
    connection_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    return {
        "id": str(connection.id),
        "name": connection.name,
        "connector_type": connection.connector_type,
        "config": connection.config,
    }


@router.put("/{connection_id}")
async def update_connection(
    connection_id: uuid.UUID,
    name: str = None,
    config: dict = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    if name:
        connection.name = name
    if config:
        connection.config = config

    await db.commit()
    return {"status": "ok"}


@router.delete("/{connection_id}")
async def delete_connection(
    connection_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    await db.delete(connection)
    await db.commit()
    return {"status": "ok"}


@router.post("/test/{connection_id}")
async def test_connection(
    connection_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    from app.services.connectors.base import get_connector

    connector = get_connector(connection.connector_type)
    try:
        success = await connector.test_connection(connection.config)
        return {"status": "ok", "success": success}
    except Exception as e:
        return {"status": "error", "success": False, "message": str(e)}
