from __future__ import annotations

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
from app.routes.admin import get_role_value
from app.routes.auth import get_current_user_optional
from app.services.report.definition import normalize_report_definition

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_role(user, *allowed):
    """Check if user has one of the allowed roles."""
    if not user:
        return False
    role = get_role_value(user)
    return role in allowed


@router.get("/", response_class=HTMLResponse)
async def designer_index(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    # Fetch reports for the index page
    if _check_role(current_user, "admin", "designer"):
        result = await db.execute(
            select(Report).order_by(Report.updated_at.desc()).limit(50)
        )
    else:
        result = await db.execute(
            select(Report).where(Report.is_active == True).order_by(Report.updated_at.desc()).limit(50)
        )
    
    reports = result.scalars().all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "designer/index.html",
        {"request": request, "current_user": current_user, "reports": reports, "mode": app_settings.MODE},
    )


@router.get("/reports")
async def list_reports(
    request: Request,
    search: str = None,
    status_filter: str = None,
    creator_filter: str = None,
    sort_by: str = "updated_at",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    from app.models.user import User as UserModel
    
    query = select(Report).options(selectinload(Report.creator))
    
    # Default to current user's reports if no filter specified
    if not creator_filter and not search and not status_filter:
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
            "mode": app_settings.MODE,
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
            "mode": app_settings.MODE,
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
            "mode": app_settings.MODE,
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
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    contents = await file.read()
    try:
        data = json.loads(contents)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    if not data.get("instantreports_export"):
        raise HTTPException(status_code=400, detail="Not a valid InstantReports export file")

    report_data = data.get("report", {})
    name = report_data.get("name", "Imported Report")
    description = report_data.get("description", "")
    definition = report_data.get("definition", {"layout": {"sections": []}, "data_sources": [], "parameters": []})

    report = Report(
        name=name,
        description=description or "",
        definition=definition,
        created_by=current_user.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

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
    with open(filepath, 'wb') as f:
        f.write(contents)

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
    import json

    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    report_def = {"layout": {"sections": []}, "data_sources": [], "parameters": []}
    if definition:
        try:
            parsed = json.loads(definition) if isinstance(definition, str) else definition
            if parsed:
                report_def = parsed
        except json.JSONDecodeError:
            logger.warning("Invalid definition JSON for new report")

    report = Report(
        name=name or "Untitled Report",
        description=description or "",
        definition=report_def,
        created_by=current_user.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from app.services.versioning import save_version

    # Use custom commit message if provided, otherwise use default
    if commit_message and commit_message.strip():
        commit_msg = f"{current_user.name} ({commit_message.strip()})" if current_user else commit_message.strip()
    else:
        commit_msg = f"Created by {current_user.name}" if current_user else "Created via designer"
    
    await save_version(db, report.id, report.definition, commit_msg, current_user.id)

    return RedirectResponse(url=f"/designer/reports/{report.id}", status_code=status.HTTP_303_SEE_OTHER)


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
    import json

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
        try:
            parsed_definition = json.loads(definition) if isinstance(definition, str) else definition
            if parsed_definition:
                report.definition = parsed_definition
        except json.JSONDecodeError:
            logger.warning(f"Invalid definition JSON for report {report_id}")

    from app.services.versioning import save_version

    # Use custom commit message if provided, otherwise use default
    if commit_message and commit_message.strip():
        commit_msg = f"{current_user.name} ({commit_message.strip()})" if current_user else commit_message.strip()
    else:
        commit_msg = f"Updated by {current_user.name}" if current_user else "Updated via designer"
    
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

    export_data = {
        "instantreports_export": True,
        "version": "1.0",
        "report": {
            "name": report.name,
            "description": report.description or "",
            "definition": report.definition or {},
        },
        "exported_by": str(current_user.id),
        "exported_at": report.updated_at.isoformat() if report.updated_at else None,
    }

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

