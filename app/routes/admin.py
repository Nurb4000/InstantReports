from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.auth import get_current_user_optional
from app.database import get_db
from app.models.connection import Delivery, DeliveryRecipient, Schedule
from app.models.report import Report, ReportOutput
from app.models.user import User

router = APIRouter()


def get_role_value(user):
    """Safely get role value from user (handles Enum or string)."""
    if hasattr(user.role, 'value'):
        return user.role.value
    return user.role


def get_auth_source_value(user):
    """Safely get auth_source value from user (handles Enum or string)."""
    if hasattr(user.auth_source, 'value'):
        return user.auth_source.value
    return user.auth_source


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    role = get_role_value(current_user) if hasattr(current_user.role, 'value') else current_user.role
    if not current_user or role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "mode": settings.MODE},
    )


@router.get("/schedules", response_class=HTMLResponse)
async def admin_schedules(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    role = get_role_value(current_user) if hasattr(current_user.role, 'value') else current_user.role
    if not current_user or role not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "admin/schedules.html",
        {"request": request, "current_user": current_user, "mode": settings.MODE},
    )


@router.get("/audit", response_class=HTMLResponse)
async def admin_audit_log(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    limit: int = Query(50, ge=1, le=200),
):
    role = get_role_value(current_user) if hasattr(current_user.role, 'value') else current_user.role
    if not current_user or role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "admin/audit.html",
        {"request": request, "current_user": current_user, "mode": settings.MODE},
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
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
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
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
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
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    await db.delete(schedule)
    await db.commit()
    return {"status": "ok"}


@router.get("/api/audit-log")
async def get_audit_log(
    limit: int = Query(50, ge=1, le=200),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get audit log entries (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.models.connection import AuditLog

    result = await db.execute(
        select(AuditLog)
        .order_by(desc(AuditLog.executed_at))
        .limit(limit)
    )
    logs = result.scalars().all()

    return [
        {
            "id": str(log.id),
            "action": log.action,
            "report_id": str(log.report_id) if log.report_id else None,
            "schedule_id": str(log.schedule_id) if log.schedule_id else None,
            "details": log.details,
            "executed_at": log.executed_at.isoformat() if log.executed_at else None,
        }
        for log in logs
    ]


@router.get("/api/schedules")
async def list_schedules(
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get all schedules (API endpoint)."""
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(Schedule).order_by(desc(Schedule.created_at))
    )
    schedules = result.scalars().all()

    return [
        {
            "id": str(s.id),
            "name": s.name,
            "report_id": str(s.report_id),
            "cron_expression": s.cron_expression,
            "timezone": s.timezone,
            "is_active": s.is_active,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in schedules
    ]


@router.get("/api/users")
async def list_users(
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get all users (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(User).order_by(desc(User.created_at))
    )
    users = result.scalars().all()

    return [
        {
            "id": str(u.id),
            "name": u.name,
            "email": u.email,
            "role": u.role.value if hasattr(u.role, 'value') else str(u.role),
            "auth_source": u.auth_source.value if hasattr(u.auth_source, 'value') else str(u.auth_source),
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/api/users")
async def create_user(
    name: str = None,
    email: str = None,
    password: str = None,
    role: str = "viewer",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.auth import create_local_user

    try:
        new_user = await create_local_user(db, email=email, name=name, password=password, role=role)
        return {"status": "ok", "id": str(new_user.id)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/users/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    name: str = None,
    email: str = None,
    role: str = None,
    is_active: bool = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Update a user (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if name:
        user.name = name
    if email:
        user.email = email
    if role:
        user.role = role
    if is_active is not None:
        user.is_active = is_active

    await db.commit()
    return {"status": "ok"}


@router.delete("/api/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    await db.delete(user)
    await db.commit()
    return {"status": "ok"}
