"""API routes for the SQL Query Builder."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.query_builder.config import QueryConfig, SchemaResponse
from app.services.query_builder.generator import validate_query
from app.services.query_builder.schema import get_schema

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
    import asyncpg
    
    # Validate configuration first
    is_valid, errors = validate_query(query_config)
    if not is_valid:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid query configuration: {errors}"
        )

    # Generate SQL
    sql = query_config.to_sql()
    
    # Add LIMIT clause
    if limit:
        sql += f"\nLIMIT {limit}"

    # Get connection config
    from sqlalchemy import text
    result = await db.execute(
        text("SELECT config FROM data_connections WHERE id = :id"),
        {"id": connection_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=404, 
            detail=f"Connection {connection_id} not found"
        )
    
    config = row[0] if isinstance(row[0], dict) else eval(row[0])
    
    try:
        # Connect to database and execute query
        conn = await asyncpg.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["database"],
        )
        
        # Execute the query
        result = await conn.fetch(sql)
        rows = [dict(row) for row in result]
        
        await conn.close()
        
        return {
            "success": True,
            "sql": sql,
            "row_count": len(rows),
            "preview": rows[:10],  # Return first 10 rows as preview
            "message": f"Query executed successfully. Returned {len(rows)} rows.",
        }
        
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
    name: str,
    description: Optional[str] = None,
    current_user: User | None = Depends(get_current_user_simple),
):
    """Save a query configuration as a template."""
    # In production, would save to database
    # For now, return success with mock ID
    import uuid
    template_id = str(uuid.uuid4())
    
    return {
        "id": template_id,
        "name": name,
        "message": f"Query template saved with ID: {template_id}",
    }


@router.get("/templates")
async def list_query_templates(
    current_user: User | None = Depends(get_current_user_simple),
):
    """List saved query templates."""
    # In production, would query database for user's templates
    return []


@router.get("/templates/{template_id}")
async def get_query_template(
    template_id: str,
    current_user: User | None = Depends(get_current_user_simple),
):
    """Get a specific query template."""
    # In production, would query database
    return None


@router.delete("/templates/{template_id}")
async def delete_query_template(
    template_id: str,
    current_user: User | None = Depends(get_current_user_simple),
):
    """Delete a query template."""
    # In production, would delete from database
    return {"message": f"Template {template_id} deleted (placeholder)"}
