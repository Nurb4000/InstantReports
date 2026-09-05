from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.report import Report, ReportOutput
from app.models.user import User
from app.routes.admin import get_role_value
from app.routes.auth import get_current_user_optional
from app.services.exporters import (
    get_file_extension,
    get_mime_type,
    normalize_output_format,
)
from app.services.report.rendering import fetch_element_data, render_report_bytes

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def portal_index(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    search: str = Query(None),
    format_type: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone

    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    from app.models.connection import Schedule
    
    # Admins see every executed output; other roles only see outputs they
    # generated or reports they own schedules for. This mirrors the report
    # catalog fix in designer.py::list_reports so admins get full oversight.
    if get_role_value(current_user) == "admin":
        query = select(ReportOutput).join(Report)
    else:
        owner_schedule_ids = (
            select(Schedule.report_id)
            .where(Schedule.owner_id == current_user.id)
        ).scalar_subquery()
        query = (
            select(ReportOutput)
            .join(Report)
            .where(
                or_(
                    ReportOutput.generated_by == current_user.id,
                    Report.id.in_(owner_schedule_ids)
                )
            )
        )

    if search:
        query = query.where(
            or_(
                Report.name.ilike(f"%{search}%"),
                ReportOutput.file_name.ilike(f"%{search}%"),
            )
        )

    if format_type:
        query = query.where(ReportOutput.format == format_type)

    if date_from:
        try:
            from_date = datetime.fromisoformat(date_from)
            if from_date.tzinfo is None:
                from_date = from_date.replace(tzinfo=timezone.utc)
            query = query.where(ReportOutput.generated_at >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.fromisoformat(date_to)
            if to_date.tzinfo is None:
                to_date = to_date.replace(tzinfo=timezone.utc)
            # Include the entire day
            to_date = to_date.replace(hour=23, minute=59, second=59)
            query = query.where(ReportOutput.generated_at <= to_date)
        except ValueError:
            pass

    query = query.order_by(ReportOutput.generated_at.desc()).limit(50)

    result = await db.execute(query)
    outputs = result.scalars().all()

    return request.app.state.templates.TemplateResponse(
        "portal/dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "outputs": outputs,
            "search": search,
            "format_type": format_type,
            "date_from": date_from,
            "date_to": date_to,
            "mode": settings.MODE,
        },
    )


@router.get("/reports/{report_id}/output/{output_id}", response_class=HTMLResponse)
async def view_report_output(
    request: Request,
    report_id: uuid.UUID,
    output_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    result = await db.execute(
        select(ReportOutput).where(ReportOutput.id == output_id, ReportOutput.report_id == report_id)
    )
    output = result.scalar_one_or_none()

    if not output:
        raise HTTPException(status_code=404, detail="Report output not found")

    return request.app.state.templates.TemplateResponse(
        "portal/view_report.html",
        {"request": request, "current_user": current_user, "output": output, "mode": settings.MODE},
    )


@router.get("/reports/{report_id}/download/{output_id}")
async def download_report_output(
    report_id: uuid.UUID,
    output_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(
        select(ReportOutput).where(ReportOutput.id == output_id, ReportOutput.report_id == report_id)
    )
    output = result.scalar_one_or_none()

    if not output:
        raise HTTPException(status_code=404, detail="Report output not found")

    return Response(
        content=output.file_data,
        media_type=output.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{output.file_name}"'},
    )


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: uuid.UUID,
    format: str = Query("pdf"),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Render a saved report against live data and stream it as a download.

    Reuses the scheduled-export render/export core (app.services.report.rendering)
    so on-demand exports stay consistent with scheduled ones, but returns the
    bytes directly instead of persisting a ReportOutput row.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.is_active.is_(True))
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        fmt = normalize_output_format(format)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format: {format!r}. Use one of: pdf, xlsx, csv, html.",
        )

    definition = report.definition or {}
    element_data = await fetch_element_data(
        definition, db, parameters=definition.get("parameters"), label=report.name
    )
    report_bytes = render_report_bytes(definition, element_data, fmt)

    return Response(
        content=report_bytes,
        media_type=get_mime_type(fmt),
        headers={
            "Content-Disposition": f'attachment; filename="{report.name}.{get_file_extension(fmt)}"'
        },
    )


@router.get("/reports/{report_id}/parameters")
async def get_report_parameters(
    report_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get parameters for a report (for dynamic parameter forms)."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    parameters = report.definition.get("parameters", [])
    return {"parameters": parameters}
