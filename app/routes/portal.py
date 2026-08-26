from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_optional
from app.database import get_db
from app.models.report import Report, ReportOutput
from app.models.user import User

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def portal_index(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    search: str = Query(None),
    format_type: str = Query(None),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    query = (
        select(ReportOutput)
        .join(Report)
        .where(ReportOutput.generated_by == current_user.id)
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

    query = query.order_by(ReportOutput.generated_at.desc()).limit(50)

    db: AsyncSession = request.state.db
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
            "mode": __import__("app.config").settings.MODE,
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
        {"request": request, "current_user": current_user, "output": output, "mode": __import__("app.config").settings.MODE},
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
