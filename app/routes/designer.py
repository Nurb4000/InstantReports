from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings as app_settings
from app.database import get_db

# Import app for static file serving
from app.models.report import Report, ReportTemplate
from app.models.user import User
from app.routes._auth_helpers import check_role, get_role_value
from app.routes.auth import get_current_user_optional
from app.services.report.definition import normalize_report_definition
from app.services.versioning import save_version

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_role(user, *allowed):
    """Check if user has one of the allowed roles."""
    return check_role(user, *allowed)


def _parse_definition_field(definition: str | None, default: dict) -> dict:
    """Parse the definition form field, returning a dict.

    Handles JSON strings from the designer editor and falls back to the
    default empty definition on parse failure.
    """
    if not definition:
        return default
    try:
        parsed = json.loads(definition) if isinstance(definition, str) else definition
        return parsed or default
    except json.JSONDecodeError:
        logger.warning("Invalid definition JSON")
        return default


def _build_commit_message(user, custom_message: str | None, action: str) -> str:
    """Build a version commit message from user + optional custom message.

    Args:
        user: The current user (may be None).
        custom_message: Optional custom commit message from the form.
        action: Past-tense action verb (e.g. "Created", "Updated").
    """
    if custom_message and custom_message.strip():
        name = user.name if user else "Unknown"
        return f"{name} ({custom_message.strip()})"
    name = user.name if user else "system"
    return f"{action} by {name}"


@router.get("/", response_class=HTMLResponse)
async def designer_index(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    # Fetch reports for the index page. Mirrors list_reports scoping: admins see
    # every report for oversight; other roles only see reports they created, so
    # lower-privilege users never leak other teams' reports into their view.
    if get_role_value(current_user) == "admin":
        result = await db.execute(
            select(Report).order_by(Report.updated_at.desc()).limit(50)
        )
    else:
        result = await db.execute(
            select(Report)
            .where(Report.created_by == current_user.id)
            .order_by(Report.updated_at.desc())
            .limit(50)
        )
    
    reports = result.scalars().all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "designer/index.html",
        {"request": request, "current_user": current_user, "reports": reports},
    )


@router.get("/reports")
async def list_reports(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    creator_filter: str | None = None,
    sort_by: str = "updated_at",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    from app.models.user import User as UserModel
    
    query = select(Report).options(selectinload(Report.creator))
    
    # Default scoping when no filter is applied. Admins see every report in the
    # system for oversight; designers/developers only see reports they created,
    # so lower-privilege users never leak other teams' reports into their view.
    if not creator_filter and not search and not status_filter and get_role_value(current_user) != "admin":
        query = query.where(Report.created_by == current_user.id)
    
    # Apply status filter
    if status_filter == "active":
        query = query.where(Report.is_active == True)
    elif status_filter == "inactive":
        query = query.where(Report.is_active == False)
    
    # Apply creator filter
    if creator_filter:
        try:
            creator_uuid = uuid.UUID(creator_filter)
            query = query.where(Report.created_by == creator_uuid)
        except ValueError:
            pass
    
    # Apply search filter
    if search:
        query = query.where(
            or_(
                Report.name.ilike(f"%{search}%"),
                Report.description.ilike(f"%{search}%"),
            )
        )
    
    # Apply sorting
    if sort_by == "name":
        query = query.order_by(Report.name.asc())
    elif sort_by == "created_at":
        query = query.order_by(Report.created_at.desc())
    else:
        query = query.order_by(Report.updated_at.desc())
    
    query = query.limit(50)
    result = await db.execute(query)
    reports = result.scalars().all()
    
    # Get all users for the creator filter dropdown
    user_result = await db.execute(select(UserModel).order_by(UserModel.name.asc()))
    users = user_result.scalars().all()
    
    return request.app.state.templates.TemplateResponse(
        "designer/index.html",
        {
            "request": request,
            "current_user": current_user,
            "reports": reports,
            "users": users,
            "filters": {
                "search": search,
                "status": status_filter,
                "creator": creator_filter,
                "sort_by": sort_by,
            },
        },
    )


@router.post("/reports/templates")
async def save_report_template(
    request: Request,
    name: str = Form(...),
    description: str = Form(None),
    definition: str = Form(None),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Save a report definition as a reusable template."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")

    report_def = normalize_report_definition(
        json.loads(definition) if definition else None
    )

    template = ReportTemplate(
        name=name,
        description=description or "",
        definition=report_def,
        created_by=current_user.id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"id": str(template.id), "name": template.name},
    )


@router.get("/reports/templates")
async def list_report_templates(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """List available report templates."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(ReportTemplate).order_by(ReportTemplate.updated_at.desc())
    )
    templates = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in templates
    ]


@router.get("/reports/templates/{template_id}")
async def get_report_template(
    template_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single report template with its full definition."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    template = await db.get(ReportTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Report template not found")

    return {
        "id": str(template.id),
        "name": template.name,
        "description": template.description,
        "definition": template.definition,
        "created_at": template.created_at.isoformat() if template.created_at else None,
    }


@router.post("/reports/from-template/{template_id}")
async def create_report_from_template(
    template_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Create a new report from a saved template and open it in the editor."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    template = await db.get(ReportTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Report template not found")

    report = Report(
        name=f"{template.name} (copy)",
        description=template.description or "",
        definition=json.loads(json.dumps(template.definition)),
        created_by=current_user.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from app.services.versioning import save_version

    commit_msg = f"Created from template '{template.name}'"
    if current_user:
        commit_msg = f"{current_user.name} - {commit_msg}"
    await save_version(db, report.id, report.definition, commit_msg, current_user.id)

    return RedirectResponse(
        url=f"/designer/reports/{report.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.delete("/reports/templates/{template_id}")
async def delete_report_template(
    template_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved report template."""
    if not current_user or not _check_role(current_user, "admin"):
        raise HTTPException(status_code=403, detail="Not authorized")

    template = await db.get(ReportTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Report template not found")

    await db.delete(template)
    await db.commit()
    return {"status": "ok"}


@router.get("/reports/new")
async def new_report_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "designer/editor.html",
        {
            "request": request,
            "current_user": current_user,
            "report": None,
            "ai_enabled": app_settings.AI_ENABLED,
        },
    )


@router.get("/reports/{report_id}")
async def edit_report_page(
    request: Request,
    report_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return request.app.state.templates.TemplateResponse(
        "designer/editor.html",
        {
            "request": request,
            "current_user": current_user,
            "report": report,
            "ai_enabled": app_settings.AI_ENABLED,
        },
    )


@router.post("/reports/import")
async def import_report(
    file: UploadFile = File(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Import a report definition from a JSON file."""
    logger.info(f"Import request received: {file.filename}")
    
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    contents = await file.read()
    logger.info(f"Read {len(contents)} bytes from {file.filename}")
    try:
        data = json.loads(contents)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in import: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    if not data.get("instantreports_export"):
        logger.warning(f"Missing instantreports_export flag in import: {data.keys()}")
        raise HTTPException(status_code=400, detail="Not a valid InstantReports export file")

    report_data = data.get("report", {})
    name = report_data.get("name", "Imported Report")
    description = report_data.get("description", "")
    definition = report_data.get("definition", {"layout": {"sections": []}, "data_sources": [], "parameters": []})

    # Auto-create connections from templates if they don't exist
    data_sources = definition.get("data_sources", [])
    logger.info(f"Importing report with {len(data_sources)} data sources")
    for ds in data_sources:
        template = ds.get("connection_template")
        
        # If no template but has connection_id, try to find or create connection
        if not template and "connection_id" in ds:
            from app.models.connection import DataConnection
            import uuid as uuid_module
            
            # Check if the referenced connection exists
            existing = await db.execute(
                select(DataConnection).where(DataConnection.id == ds["connection_id"])
            )
            conn = existing.scalar_one_or_none()
            
            if not conn:
                logger.info(f"Referenced connection {ds['connection_id']} not found, creating new")
                # Create a default connection based on connector_type
                conn = DataConnection(
                    id=uuid_module.uuid4(),
                    name=ds.get("name", "Imported Connection"),
                    connector_type=ds.get("connector_type", "postgresql"),
                    config={
                        "host": "postgres",
                        "port": 5432,
                        "database": "northwind",
                        "user": "northwind",
                    },
                    created_by=current_user.id,
                )
                db.add(conn)
                await db.commit()
                ds["connection_id"] = str(conn.id)
                logger.info(f"Created new connection: {conn.id}")
        # If has template, create or reuse connection
        elif template:
            from app.models.connection import DataConnection
            import uuid as uuid_module
            
            # Check if connection with same name already exists
            existing = await db.execute(
                select(DataConnection).where(DataConnection.name == template.get("name", "Imported Connection"))
            )
            conn = existing.scalar_one_or_none()
            
            if not conn:
                logger.info(f"Creating new connection: {template.get('name')}")
                conn = DataConnection(
                    id=uuid_module.uuid4(),
                    name=template.get("name", "Imported Connection"),
                    connector_type=template.get("connector_type", ds.get("connector_type", "postgresql")),
                    config={
                        "host": template.get("host", "localhost"),
                        "port": template.get("port", 5432),
                        "database": template.get("database", ""),
                        "user": template.get("user", ""),
                    },
                    created_by=current_user.id,
                )
                db.add(conn)
                await db.commit()
                logger.info(f"Created connection: {conn.id}")
            
            # Update the data source with the connection ID
            ds["connection_id"] = str(conn.id)

    report = Report(
        name=name,
        description=description or "",
        definition=definition,
        created_by=current_user.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    
    logger.info(f"Imported report: {report.id} - {name}")

    return RedirectResponse(url=f"/designer/reports/{report.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/images/upload")
async def upload_image(
    file: UploadFile = File(...),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Upload an image for use in reports."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Validate file type
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    # Generate unique filename
    import uuid
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
    filename = f"{uuid.uuid4()}.{ext}"

    # Save to static/img directory
    from pathlib import Path
    upload_dir = Path(app_settings.STATIC_DIR) / "img"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filepath = upload_dir / filename

    contents = await file.read()
    await asyncio.to_thread(filepath.write_bytes, contents)

    return {
        "status": "ok",
        "url": f"/static/img/{filename}",
        "filename": filename,
        "size": len(contents),
    }


@router.post("/reports")
async def create_report(
    request: Request,
    name: str = Form(None),
    description: str = Form(None),
    definition: str = Form(None),
    commit_message: str = Form(None),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    default_def = {"layout": {"sections": []}, "data_sources": [], "parameters": []}
    report_def = _parse_definition_field(definition, default_def)

    report = Report(
        name=name or "Untitled Report",
        description=description or "",
        definition=report_def,
        created_by=current_user.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    commit_msg = _build_commit_message(current_user, commit_message, "Created")
    await save_version(db, report.id, report.definition, commit_msg, current_user.id)

    return RedirectResponse(url=f"/designer/reports/{report.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reports/{report_id}/toggle-status")
async def toggle_report_status(
    report_id: uuid.UUID,
    is_active: bool = Form(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Toggle a report's active/inactive status."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Only allow editing your own reports (unless admin)
    user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    if user_role != "admin" and report.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this report")

    report.is_active = is_active
    await db.commit()
    return {"status": "ok", "is_active": is_active}


@router.post("/reports/{report_id}")
async def update_report(
    request: Request,
    report_id: uuid.UUID,
    name: str = Form(None),
    description: str = Form(None),
    definition: str = Form(None),
    commit_message: str = Form(None),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if name:
        report.name = name
    if description is not None:
        report.description = description
    if definition:
        parsed_definition = _parse_definition_field(definition, report.definition)
        report.definition = parsed_definition

    commit_msg = _build_commit_message(current_user, commit_message, "Updated")
    await save_version(db, report.id, report.definition, commit_msg, current_user.id)

    await db.commit()
    return RedirectResponse(url=f"/designer/reports/{report_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/reports/{report_id}")
async def delete_report(
    request: Request,
    report_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or not _check_role(current_user, "admin"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await db.delete(report)
    await db.commit()
    return {"status": "ok"}


def _build_report_export_data(report, current_user, db=None) -> dict:
    """Build the export dict for a report definition.

    Pure function: no DB or HTTP state. Testable in isolation.
    Includes connection templates so exports are self-contained.
    """
    definition = report.definition or {}
    
    # Enrich data sources with connection templates
    data_sources = definition.get("data_sources", [])
    for ds in data_sources:
        if "connection_id" in ds and db is not None:
            from app.models.connection import DataConnection
            from sqlalchemy import select
            
            result = db.execute(select(DataConnection).where(DataConnection.id == ds["connection_id"]))
            conn = result.scalar_one_or_none()
            
            if conn:
                # Create a template without sensitive info
                ds["connection_template"] = {
                    "name": conn.name,
                    "connector_type": conn.connector_type,
                    **{k: v for k, v in conn.config.items() if k not in ("password", "secret")},
                }
                # Remove the connection_id since we're embedding the template
                del ds["connection_id"]
    
    definition["data_sources"] = data_sources
    
    return {
        "instantreports_export": True,
        "version": "1.0",
        "report": {
            "name": report.name,
            "description": report.description or "",
            "definition": definition,
        },
        "exported_by": str(current_user.id),
        "exported_at": report.updated_at.isoformat() if report.updated_at else None,
    }


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Export a report definition as a JSON file."""
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    export_data = _build_report_export_data(report, current_user, db)

    import tempfile
    fd, path = tempfile.mkstemp(suffix=".ir.json")
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
        return FileResponse(
            path,
            media_type="application/json",
            filename=f"{report.name.replace(' ', '_')}_export.json",
        )
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise

