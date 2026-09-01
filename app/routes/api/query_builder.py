"""API routes for the SQL Query Builder."""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.connection import DataConnection, QueryTemplate
from app.models.user import User
from app.services.ai.client import AIClient, AISQLGenerator
from app.services.query_builder.config import QueryConfig
from app.services.query_builder.generator import validate_query
from app.services.query_builder.history import (
    delete_snapshot,
    get_snapshot,
    list_snapshots,
    save_snapshot,
)
from app.services.query_builder.adapter import execute_query
from app.services.query_builder.optimizer import analyze_query
from app.services.query_builder.schema import get_schema
from app.services.query_builder.sql_parser import parse_sql_to_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/query-builder", tags=["query-builder"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user_simple(request: Request) -> User | None:
    """Simple current user dependency without database."""
    return None


@router.get("/schema/{connection_id}")
async def get_schema_endpoint(
    connection_id: str,
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Get schema information for a database connection."""
    schema = await get_schema(db, connection_id)
    
    if not schema:
        raise HTTPException(
            status_code=404, 
            detail=f"Connection {connection_id} not found or unavailable"
        )
    
    return schema


@router.post("/validate")
async def validate_query_endpoint(
    query_config: QueryConfig,
    current_user: User | None = Depends(get_current_user_simple),
):
    """Validate a query configuration."""
    is_valid, errors = validate_query(query_config)

    return {
        "valid": is_valid,
        "errors": errors,
        "sql": query_config.to_sql() if is_valid else None,
    }


@router.post("/generate-sql")
async def generate_sql_endpoint(
    query_config: QueryConfig,
    current_user: User | None = Depends(get_current_user_simple),
):
    """Generate SQL from a query configuration."""
    sql = query_config.to_sql()
    return {"sql": sql}


@router.post("/test")
async def test_query_endpoint(
    query_config: QueryConfig,
    connection_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Test a query against the database."""
    # Validate configuration first
    is_valid, errors = validate_query(query_config)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid query configuration: {errors}"
        )

    # Generate SQL
    sql = query_config.to_sql()

    # Load the connection to determine connector type + config
    try:
        conn_uuid = uuid.UUID(connection_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid connection_id")

    result = await db.execute(
        select(DataConnection).where(DataConnection.id == conn_uuid)
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=404, detail=f"Connection {connection_id} not found"
        )

    try:
        rows = await execute_query(
            connection.connector_type,
            connection.config,
            sql,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        return {
            "success": False,
            "sql": sql,
            "row_count": 0,
            "preview": [],
            "message": f"Query execution failed: {str(e)}",
            "error": str(e),
        }


@router.post("/save")
async def save_query_template(
    query_config: QueryConfig,
    name: str = Query(...),
    description: Optional[str] = Query(None),
    connection_id: str = Query(...),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Persist a query configuration as a reusable template."""
    try:
        tmpl_uuid = uuid.UUID(connection_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid connection_id")

    result = await db.execute(
        select(DataConnection.id).where(DataConnection.id == tmpl_uuid)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")

    created_by = current_user.id if current_user else None

    template = QueryTemplate(
        name=name,
        description=description,
        query_config=query_config.model_dump(mode="json"),
        connection_id=tmpl_uuid,
        created_by=created_by,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return {
        "id": str(template.id),
        "name": template.name,
        "connection_id": str(template.connection_id),
        "message": f"Query template saved with ID: {template.id}",
    }


@router.get("/templates")
async def list_query_templates(
    connection_id: Optional[str] = Query(None),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """List saved query templates, optionally filtered by connection."""
    query = select(QueryTemplate)

    if connection_id:
        try:
            query = query.where(QueryTemplate.connection_id == uuid.UUID(connection_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid connection_id")

    query = query.order_by(QueryTemplate.updated_at.desc())
    result = await db.execute(query)
    templates = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "connection_id": str(t.connection_id),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in templates
    ]


@router.get("/templates/{template_id}")
async def get_query_template(
    template_id: str,
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a specific query template with its full configuration."""
    try:
        tmpl_uuid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid template_id")

    template = await db.get(QueryTemplate, tmpl_uuid)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "id": str(template.id),
        "name": template.name,
        "description": template.description,
        "connection_id": str(template.connection_id),
        "query_config": template.query_config,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }


@router.delete("/templates/{template_id}")
async def delete_query_template(
    template_id: str,
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved query template."""
    try:
        tmpl_uuid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid template_id")

    template = await db.get(QueryTemplate, tmpl_uuid)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(template)
    await db.commit()
    return {"message": f"Template {template_id} deleted", "id": str(template_id)}


@router.post("/history")
async def save_query_history(
    query_config: QueryConfig,
    connection_id: str = Query(...),
    report_id: Optional[str] = Query(None),
    element_id: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Record a snapshot of the current query configuration."""
    try:
        tmpl_conn = uuid.UUID(connection_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid connection_id")

    result = await db.execute(
        select(DataConnection.id).where(DataConnection.id == tmpl_conn)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")

    tmpl_report = uuid.UUID(report_id) if report_id else None
    created_by = current_user.id if current_user else None

    snapshot = await save_snapshot(
        db,
        query_config.model_dump(mode="json"),
        report_id=tmpl_report,
        element_id=element_id,
        connection_id=tmpl_conn,
        label=label,
        user_id=created_by,
    )

    return {
        "id": str(snapshot.id),
        "message": f"Query history snapshot saved: {snapshot.id}",
    }


@router.get("/history")
async def list_query_history(
    connection_id: Optional[str] = Query(None),
    report_id: Optional[str] = Query(None),
    element_id: Optional[str] = Query(None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """List query-history snapshots for a connection/report/element."""
    def _to_uuid(value: Optional[str], name: str) -> Optional[uuid.UUID]:
        if value is None:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid {name}")

    conn_uuid = _to_uuid(connection_id, "connection_id")
    report_uuid = _to_uuid(report_id, "report_id")

    snapshots = await list_snapshots(
        db,
        report_id=report_uuid,
        element_id=element_id,
        connection_id=conn_uuid,
        limit=limit,
    )

    return [
        {
            "id": str(s.id),
            "label": s.label,
            "report_id": str(s.report_id) if s.report_id else None,
            "element_id": s.element_id,
            "connection_id": str(s.connection_id) if s.connection_id else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in snapshots
    ]


@router.get("/history/{snapshot_id}")
async def get_query_history(
    snapshot_id: str,
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single query-history snapshot with its full configuration."""
    try:
        tmpl_uuid = uuid.UUID(snapshot_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid snapshot_id")

    snapshot = await get_snapshot(db, tmpl_uuid)
    if not snapshot:
        raise HTTPException(status_code=404, detail="History snapshot not found")

    return {
        "id": str(snapshot.id),
        "label": snapshot.label,
        "report_id": str(snapshot.report_id) if snapshot.report_id else None,
        "element_id": snapshot.element_id,
        "connection_id": str(snapshot.connection_id) if snapshot.connection_id else None,
        "query_config": snapshot.query_config,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


@router.delete("/history/{snapshot_id}")
async def delete_query_history(
    snapshot_id: str,
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Delete a query-history snapshot."""
    try:
        tmpl_uuid = uuid.UUID(snapshot_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid snapshot_id")

    removed = await delete_snapshot(db, tmpl_uuid)
    if not removed:
        raise HTTPException(status_code=404, detail="History snapshot not found")

    return {"message": f"History snapshot {snapshot_id} deleted", "id": str(snapshot_id)}


@router.post("/optimize")
async def optimize_query_endpoint(
    query_config: QueryConfig,
    connection_id: Optional[str] = Query(None),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Analyze a query configuration and return optimization suggestions."""
    schema = None
    if connection_id:
        try:
            uuid.UUID(connection_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid connection_id")
        schema = await get_schema(db, connection_id)

    sql = query_config.to_sql()
    suggestions = analyze_query(query_config, schema)

    return {
        "sql": sql,
        "suggestions": suggestions,
        "score": max(0, 100 - len(suggestions) * 15),
    }


@router.post("/nl-to-query")
async def nl_to_query_endpoint(
    request: Request,
    connection_id: str = Query(...),
    current_user: User | None = Depends(get_current_user_simple),
    db: AsyncSession = Depends(get_db),
):
    """Generate a QueryConfig from a natural-language prompt for a connection."""
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    try:
        conn_uuid = uuid.UUID(connection_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid connection_id")

    connection = (
        await db.execute(select(DataConnection).where(DataConnection.id == conn_uuid))
    ).scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")

    if not settings.AI_ENABLED:
        raise HTTPException(
            status_code=503, detail="AI is not configured. Set AI_ENABLED=true."
        )

    schema = await get_schema(db, connection_id)
    if not schema:
        raise HTTPException(
            status_code=500, detail=f"Unable to fetch schema for connection {connection_id}"
        )

    client = AIClient(
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL,
    )
    generator = AISQLGenerator(client)
    sql = await generator.generate_sql(
        prompt, schema.model_dump(mode="json"), connection.connector_type
    )

    query_config = parse_sql_to_config(sql)
    return {
        "sql": sql,
        "query_config": query_config.model_dump(mode="json"),
    }
