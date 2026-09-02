from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.connection import DataConnection
from app.models.user import User
from app.routes.auth import get_current_user_optional

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_designer(user):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Get role value (handle both Enum and string)
    role = user.role.value if hasattr(user.role, 'value') else user.role
    if role not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")


@router.get("/")
async def list_connections(
    search: str = None,
    type_filter: str = None,
    sort_by: str = "updated_at",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    query = select(DataConnection)
    
    # Apply type filter
    if type_filter:
        query = query.where(DataConnection.connector_type == type_filter)
    
    # Apply search filter
    if search:
        query = query.where(DataConnection.name.ilike(f"%{search}%"))
    
    # Apply sorting
    if sort_by == "name":
        query = query.order_by(DataConnection.name.asc())
    elif sort_by == "created_at":
        query = query.order_by(DataConnection.created_at.desc())
    else:
        query = query.order_by(DataConnection.updated_at.desc())
    
    result = await db.execute(query.limit(50))
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


@router.get("/connectors")
async def list_connectors(
    current_user: User | None = Depends(get_current_user_optional),
):
    """List available connector types with their config field definitions."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.services.connectors.base import ConnectorFactory

    connector_types = ConnectorFactory.list_connectors()
    connectors = []
    for ct in connector_types:
        try:
            conn = ConnectorFactory.get_connector(ct)
            connectors.append({
                "type": ct,
                "label": ct.replace("_", " ").title(),
                "fields": getattr(conn, "config_fields", []),
            })
        except Exception as e:
            logger.warning(f"Failed to instantiate connector {ct}: {e}")
            connectors.append({
                "type": ct,
                "label": ct.replace("_", " ").title(),
                "fields": [],
            })

    return connectors


@router.post("/")
async def create_connection(
    data: dict = Body(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    _require_designer(current_user)

    connection = DataConnection(
        name=data.get("name") or "Unnamed Connection",
        connector_type=data.get("connector_type") or "postgresql",
        config=data.get("config") or {},
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
    data: dict = Body(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    _require_designer(current_user)

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    if "name" in data:
        connection.name = data["name"]
    if "config" in data:
        connection.config = data["config"]

    await db.commit()
    return {"status": "ok"}


@router.delete("/{connection_id}")
async def delete_connection(
    connection_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    if role != "admin":
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
    _require_designer(current_user)

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


@router.get("/{connection_id}/schema")
async def get_schema(
    connection_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get available tables and columns for a connection."""
    _require_designer(current_user)

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    from app.services.connectors.base import get_connector

    connector = get_connector(connection.connector_type)
    try:
        schema = await connector.get_schema(connection.config)
        return {"status": "ok", "schema": schema}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{connection_id}/query")
async def test_query(
    connection_id: uuid.UUID,
    data: dict = Body(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Execute a test query and return column info + sample rows."""
    _require_designer(current_user)

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    from app.services.connectors.base import get_connector

    connector = get_connector(connection.connector_type)
    query = data.get("query", "")
    parameters = data.get("parameters", {})
    limit = data.get("limit", 100)

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    try:
        df = await connector.execute_query(connection.config, query, parameters or None)
        if limit and len(df) > limit:
            df = df.head(limit)

        columns = []
        for col in df.columns:
            columns.append({
                "name": str(col),
                "type": str(df[col].dtype),
                "sample_values": df[col].dropna().head(5).tolist(),
            })

        return {
            "status": "ok",
            "columns": columns,
            "row_count": len(df),
            "preview": df.to_dict(orient="records"),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e!s}")


@router.post("/{connection_id}/calculate")
async def calculate_field(
    connection_id: uuid.UUID,
    data: dict = Body(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate a calculated-field expression against sample data from a connection."""
    _require_designer(current_user)

    expression = (data.get("expression") or "").strip()
    if not expression:
        raise HTTPException(status_code=400, detail="Expression is required")

    result = await db.execute(select(DataConnection).where(DataConnection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    from app.services.connectors.base import get_connector

    connector = get_connector(connection.connector_type)
    query = (data.get("query") or "").strip()
    limit = data.get("limit", 50)

    try:
        if not query:
            schema = await connector.get_schema(connection.config)
            tables = schema.get("tables", []) if isinstance(schema, dict) else []
            if not tables:
                raise HTTPException(status_code=400, detail="No tables available to sample from this connection")
            first_table = tables[0].get("name") if isinstance(tables[0], dict) else tables[0]
            query = f"SELECT * FROM {first_table}"
            if limit:
                query += f" LIMIT {int(limit)}"

        df = await connector.execute_query(connection.config, query)
        df = df.head(limit) if limit and len(df) > limit else df

        from app.services.engine.calculated_fields import CalculatedFieldEvaluator
        evaluator = CalculatedFieldEvaluator()
        series = evaluator.evaluate(expression, df)
        preview = [None if v is None else (float(v) if isinstance(v, (int, float)) else v) for v in list(series)]
        return {"status": "ok", "expression": expression, "preview": preview[:int(limit or 50)]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Calculation error: {e!s}")
