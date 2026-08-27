from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.auth import get_current_user_optional
from app.config import settings
from app.database import get_db
from app.models.report import Report
from app.models.user import User
from app.routes.admin import get_role_value

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
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "designer/index.html",
        {"request": request, "current_user": current_user, "mode": settings.MODE},
    )


@router.get("/reports")
async def list_reports(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    if _check_role(current_user, "admin", "designer"):
        result = await db.execute(
            select(Report).order_by(Report.updated_at.desc()).limit(50)
        )
    else:
        result = await db.execute(
            select(Report).where(Report.is_active == True).order_by(Report.updated_at.desc()).limit(50)
        )

    reports = result.scalars().all()
    return request.app.state.templates.TemplateResponse(
        "designer/reports.html",
        {"request": request, "current_user": current_user, "reports": reports, "mode": settings.MODE},
    )


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
            "mode": settings.MODE,
            "ai_enabled": settings.AI_ENABLED,
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
            "mode": settings.MODE,
            "ai_enabled": settings.AI_ENABLED,
        },
    )


@router.post("/reports")
async def create_report(
    request: Request,
    name: str = None,
    description: str = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or not _check_role(current_user, "admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    report = Report(
        name=name or "Untitled Report",
        description=description or "",
        definition={"layout": {"sections": []}, "data_sources": [], "parameters": []},
        created_by=current_user.id,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return RedirectResponse(url=f"/designer/reports/{report.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reports/{report_id}")
async def update_report(
    request: Request,
    report_id: uuid.UUID,
    name: str = None,
    description: str = None,
    definition: dict = None,
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
        report.definition = definition

    await db.commit()
    return {"status": "ok"}


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
