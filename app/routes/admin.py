from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_optional
from app.database import get_db
from app.models.connection import Delivery, DeliveryRecipient, Schedule
from app.models.report import Report
from app.models.user import User

router = APIRouter()


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user or current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "mode": __import__("app.config").settings.MODE},
    )


@router.get("/schedules", response_class=HTMLResponse)
async def admin_schedules(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "admin/schedules.html",
        {"request": request, "current_user": current_user, "mode": __import__("app.config").settings.MODE},
    )


@router.post("/schedules")
async def create_schedule(
    request: Request,
    report_id: uuid.UUID = None,
    name: str = None,
    cron_expression: str = None,
    timezone: str = "UTC",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    schedule = Schedule(
        report_id=report_id,
        name=name or "Unnamed Schedule",
        cron_expression=cron_expression,
        timezone=timezone,
        created_by=current_user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    return {"status": "ok", "id": str(schedule.id)}


@router.get("/schedules/{schedule_id}")
async def get_schedule(
    schedule_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "report_id": str(schedule.report_id),
        "cron_expression": schedule.cron_expression,
        "timezone": schedule.timezone,
        "is_active": schedule.is_active,
    }


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: uuid.UUID,
    name: str = None,
    cron_expression: str = None,
    is_active: bool = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if name:
        schedule.name = name
    if cron_expression is not None:
        schedule.cron_expression = cron_expression
    if is_active is not None:
        schedule.is_active = is_active

    await db.commit()
    return {"status": "ok"}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or current_user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    await db.delete(schedule)
    await db.commit()
    return {"status": "ok"}
