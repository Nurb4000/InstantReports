from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.connection import Schedule
from app.models.user import User
from app.routes.auth import get_current_user_optional

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
        {
            "request": request,
            "current_user": current_user,
            "mode": settings.MODE,
            "current_user_id": str(current_user.id) if current_user else None,
        },
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
    report_id: str = Form(None),
    name: str = Form(None),
    cron_expression: str = Form(None),
    timezone: str = Form("UTC"),
    output_format: str = Form("pdf"),
    delivery_type: str = Form("email"),
    recipient_emails: str = Form(""),
    owner_id: str = Form(None),
    # SFTP fields
    sftp_host: str = Form(None),
    sftp_port: str = Form(None),
    sftp_username: str = Form(None),
    sftp_password: str = Form(None),
    sftp_remote_path: str = Form(None),
    # SMB fields
    smb_server: str = Form(None),
    smb_share: str = Form(None),
    smb_username: str = Form(None),
    smb_password: str = Form(None),
    smb_remote_path: str = Form(None),
    # Webhook fields
    webhook_url: str = Form(None),
    webhook_secret: str = Form(None),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Parse JSON body if content-type is application/json
    if request.headers.get('content-type') == 'application/json':
        body = await request.json()
        report_id = body.get('report_id', report_id)
        name = body.get('name', name)
        cron_expression = body.get('cron_expression', cron_expression)
        timezone = body.get('timezone', timezone)
        output_format = body.get('output_format', output_format)
        delivery_type = body.get('delivery_type', delivery_type)
        recipient_emails = body.get('recipient_emails', recipient_emails)
        owner_id = body.get('owner_id', owner_id)
        sftp_host = body.get('sftp_host', sftp_host)
        sftp_port = body.get('sftp_port', sftp_port)
        sftp_username = body.get('sftp_username', sftp_username)
        sftp_password = body.get('sftp_password', sftp_password)
        sftp_remote_path = body.get('sftp_remote_path', sftp_remote_path)
        smb_server = body.get('smb_server', smb_server)
        smb_share = body.get('smb_share', smb_share)
        smb_username = body.get('smb_username', smb_username)
        smb_password = body.get('smb_password', smb_password)
        smb_remote_path = body.get('smb_remote_path', smb_remote_path)
        webhook_url = body.get('webhook_url', webhook_url)
        webhook_secret = body.get('webhook_secret', webhook_secret)

    # Parse recipient emails
    emails = [e.strip() for e in recipient_emails.split(",") if e.strip()] if recipient_emails else []

    # Use owner_id if provided, otherwise default to the creating user (admin)
    schedule_owner_id = uuid.UUID(owner_id) if owner_id else current_user.id

    # Build delivery config based on type
    delivery_config = {
        "type": delivery_type,
        "emails": emails if delivery_type == "email" else [],
    }

    # Add SFTP/SMB/Webhook specific config if provided
    if delivery_type == "sftp":
        delivery_config.update({
            "host": sftp_host or "",
            "port": int(sftp_port) if sftp_port else 22,
            "username": sftp_username or "",
            "password": sftp_password or "",
            "remote_path": sftp_remote_path or "/",
        })
    elif delivery_type == "smb":
        delivery_config.update({
            "server": smb_server or "",
            "share": smb_share or "",
            "username": smb_username or "",
            "password": smb_password or "",
            "remote_path": smb_remote_path or "/",
        })
    elif delivery_type == "webhook":
        delivery_config.update({
            "url": webhook_url or "",
            "secret": webhook_secret or "",
        })

    if not report_id:
        raise HTTPException(status_code=400, detail="report_id is required")
    
    try:
        report_uuid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report_id format")

    schedule = Schedule(
        report_id=report_uuid,
        name=name or "Unnamed Schedule",
        cron_expression=cron_expression or "",
        timezone=timezone,
        output_format=output_format,
        delivery_type=delivery_type,
        delivery_config=delivery_config,
        recipient_emails=recipient_emails if delivery_type == "email" else None,
        owner_id=schedule_owner_id,
        created_by=current_user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    return {"status": "ok", "id": str(schedule.id)}


@router.post("/schedules/test-connection")
async def test_delivery_connection(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Test a delivery connection (SFTP/SMB/Webhook) without saving a schedule."""
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    if request.headers.get('content-type') == 'application/json':
        body = await request.json()
    else:
        body = {}

    delivery_type = (body.get('delivery_type') or '').strip().lower()
    valid_types = ("sftp", "smb", "webhook")
    if delivery_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid delivery type. Must be one of {valid_types}")

    try:
        if delivery_type == "sftp":
            from app.services.delivery.sftp import test_connection as test_sftp
            success, message = await test_sftp(
                host=(body.get('sftp_host') or '').strip(),
                port=int((body.get('sftp_port') or 22) or 22),
                username=(body.get('sftp_username') or '').strip(),
                password=(body.get('sftp_password') or None),
            )
        elif delivery_type == "smb":
            from app.services.delivery.smb_webhook import test_smb_connection
            success, message = await test_smb_connection(
                server=(body.get('smb_server') or '').strip(),
                share=(body.get('smb_share') or '').strip(),
                username=(body.get('smb_username') or '').strip(),
                password=(body.get('smb_password') or ''),
                remote_path=(body.get('smb_remote_path') or '/'),
            )
        elif delivery_type == "webhook":
            from app.services.delivery.smb_webhook import test_webhook_connection
            success, message = await test_webhook_connection(
                url=(body.get('webhook_url') or '').strip(),
                secret=(body.get('webhook_secret') or None),
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {e}")

    return {"success": success, "message": message, "delivery_type": delivery_type}


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
    request: Request,
    schedule_id: uuid.UUID,
    name: str = Form(None),
    cron_expression: str = Form(None),
    timezone: str = Form(None),
    output_format: str = Form(None),
    delivery_type: str = Form(None),
    recipient_emails: str = Form(None),
    owner_id: str = Form(None),
    is_active: str = Form(None),
    # SFTP fields
    sftp_host: str = Form(None),
    sftp_port: str = Form(None),
    sftp_username: str = Form(None),
    sftp_password: str = Form(None),
    sftp_remote_path: str = Form(None),
    # SMB fields
    smb_server: str = Form(None),
    smb_share: str = Form(None),
    smb_username: str = Form(None),
    smb_password: str = Form(None),
    smb_remote_path: str = Form(None),
    # Webhook fields
    webhook_url: str = Form(None),
    webhook_secret: str = Form(None),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Parse JSON body if content-type is application/json
    if request.headers.get('content-type') == 'application/json':
        body = await request.json()
        name = body.get('name', name)
        cron_expression = body.get('cron_expression', cron_expression)
        timezone = body.get('timezone', timezone)
        output_format = body.get('output_format', output_format)
        delivery_type = body.get('delivery_type', delivery_type)
        recipient_emails = body.get('recipient_emails', recipient_emails)
        owner_id = body.get('owner_id', owner_id)
        is_active = body.get('is_active', is_active)
        sftp_host = body.get('sftp_host', sftp_host)
        sftp_port = body.get('sftp_port', sftp_port)
        sftp_username = body.get('sftp_username', sftp_username)
        sftp_password = body.get('sftp_password', sftp_password)
        sftp_remote_path = body.get('sftp_remote_path', sftp_remote_path)
        smb_server = body.get('smb_server', smb_server)
        smb_share = body.get('smb_share', smb_share)
        smb_username = body.get('smb_username', smb_username)
        smb_password = body.get('smb_password', smb_password)
        smb_remote_path = body.get('smb_remote_path', smb_remote_path)
        webhook_url = body.get('webhook_url', webhook_url)
        webhook_secret = body.get('webhook_secret', webhook_secret)

    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Parse is_active from string or boolean
    active_value = None
    if is_active is not None:
        if isinstance(is_active, bool):
            active_value = is_active
        elif isinstance(is_active, str):
            active_value = is_active.lower() in ('true', '1', 'on')

    # Parse owner_id from string to UUID
    parsed_owner_id = None
    if owner_id:
        try:
            parsed_owner_id = uuid.UUID(owner_id)
        except ValueError:
            pass

    if name:
        schedule.name = name
    if cron_expression is not None:
        schedule.cron_expression = cron_expression
    if timezone:
        schedule.timezone = timezone
    if output_format:
        schedule.output_format = output_format
    if delivery_type:
        schedule.delivery_type = delivery_type
        
        # Build delivery config based on type
        delivery_config = {"type": delivery_type}
        
        if delivery_type == "email":
            emails = [e.strip() for e in (recipient_emails or "").split(",") if e.strip()] if recipient_emails else []
            delivery_config["emails"] = emails
            schedule.recipient_emails = recipient_emails
        elif delivery_type == "sftp":
            delivery_config.update({
                "host": sftp_host,
                "port": int(sftp_port) if sftp_port else 22,
                "username": sftp_username,
                "password": sftp_password,
                "remote_path": sftp_remote_path or "/",
            })
        elif delivery_type == "smb":
            delivery_config.update({
                "server": smb_server,
                "share": smb_share,
                "username": smb_username,
                "password": smb_password,
                "remote_path": smb_remote_path or "/",
            })
        elif delivery_type == "webhook":
            delivery_config.update({
                "url": webhook_url,
                "secret": webhook_secret,
            })
        
        schedule.delivery_config = delivery_config
    if parsed_owner_id:
        schedule.owner_id = parsed_owner_id
    if active_value is not None:
        schedule.is_active = active_value

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
    search: str | None = None,
    action_filter: str | None = None,
    sort_by: str = "executed_at",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get audit log entries (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.models.connection import AuditLog

    query = select(AuditLog)
    
    # Apply action filter
    if action_filter:
        query = query.where(AuditLog.action == action_filter)
    
    # Apply search filter (search in details JSON)
    if search:
        query = query.where(AuditLog.details.ilike(f"%{search}%"))
    
    # Apply sorting
    if sort_by == "action":
        query = query.order_by(AuditLog.action.asc())
    else:
        query = query.order_by(desc(AuditLog.executed_at))
    
    result = await db.execute(query.limit(limit))
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
    search: str | None = None,
    status_filter: str | None = None,
    sort_by: str = "created_at",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get all schedules (API endpoint)."""
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.models.report import Report
    
    query = select(Schedule).options(selectinload(Schedule.report), selectinload(Schedule.owner))
    
    # Apply status filter
    if status_filter == "active":
        query = query.where(Schedule.is_active == True)
    elif status_filter == "inactive":
        query = query.where(Schedule.is_active == False)
    
    # Apply search filter (search in schedule name or report name)
    if search:
        query = query.where(
            or_(
                Schedule.name.ilike(f"%{search}%"),
                Report.name.ilike(f"%{search}%"),
            )
        ).join(Report, Report.id == Schedule.report_id)
    
    # Apply sorting
    if sort_by == "name":
        query = query.order_by(Schedule.name.asc())
    elif sort_by == "updated_at":
        query = query.order_by(Schedule.updated_at.desc())
    else:
        query = query.order_by(desc(Schedule.created_at))
    
    result = await db.execute(query.limit(100))
    schedules = result.scalars().all()

    return [
        {
            "id": str(s.id),
            "name": s.name,
            "report_id": str(s.report_id),
            "report_name": s.report.name if s.report else "Unknown",
            "cron_expression": s.cron_expression,
            "timezone": s.timezone,
            "output_format": s.output_format or "pdf",
            "delivery_type": s.delivery_type or "email",
            "delivery_config": s.delivery_config or {},
            "recipient_emails": s.recipient_emails or "",
            "owner_id": str(s.owner_id) if s.owner_id else None,
            "owner_name": s.owner.name if s.owner else "Unknown",
            "is_active": s.is_active,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in schedules
    ]


@router.get("/api/schedules/{schedule_id}")
async def get_schedule_api(
    schedule_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get a single schedule (API endpoint)."""
    if not current_user or get_role_value(current_user) not in ("admin", "designer"):
        raise HTTPException(status_code=403, detail="Not authorized")

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
        "output_format": schedule.output_format or "pdf",
        "delivery_type": schedule.delivery_type or "email",
        "delivery_config": schedule.delivery_config or {},
        "recipient_emails": schedule.recipient_emails or "",
        "owner_id": str(schedule.owner_id) if schedule.owner_id else None,
        "is_active": schedule.is_active,
    }


@router.get("/api/users")
async def list_users(
    search: str | None = None,
    role_filter: str | None = None,
    status_filter: str | None = None,
    sort_by: str = "created_at",
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get all users (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    query = select(User)
    
    # Apply role filter
    if role_filter:
        query = query.where(User.role == role_filter)
    
    # Apply status filter
    if status_filter == "active":
        query = query.where(User.is_active == True)
    elif status_filter == "inactive":
        query = query.where(User.is_active == False)
    
    # Apply search filter
    if search:
        query = query.where(
            or_(
                User.name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
            )
        )
    
    # Apply sorting
    if sort_by == "name":
        query = query.order_by(User.name.asc())
    elif sort_by == "email":
        query = query.order_by(User.email.asc())
    else:
        query = query.order_by(desc(User.created_at))
    
    result = await db.execute(query)
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
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
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


@router.get("/api/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user (API endpoint)."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "role": get_role_value(user),
        "auth_source": get_auth_source_value(user),
        "is_active": user.is_active,
    }


@router.put("/api/users/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    name: str = Form(None),
    email: str = Form(None),
    password: str = Form(None),
    role: str = Form(None),
    is_active: bool = Form(None),
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
    if password:
        from app.auth import hash_password
        user.password_hash = hash_password(password)
    if role:
        user.role = role
    if is_active is not None:
        user.is_active = is_active

    await db.commit()
    return {"status": "ok"}


@router.post("/api/users/{user_id}/change-password")
async def change_password(
    user_id: uuid.UUID,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's password (self-service or admin)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Allow users to change their own password, or admins to change any password
    is_admin = get_role_value(current_user) == "admin"
    is_self = current_user.id == user_id

    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.auth import verify_password
    if not verify_password(current_password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.password_hash = __import__("app.auth").hash_password(new_password)
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
