from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.routes._auth_helpers import get_role_value
from app.routes.auth import get_current_user_optional
from app.services.delivery.email import send_email
from app.services.settings_store import save_setting_updates

router = APIRouter()


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Admin settings page for SMTP, LDAP, and AI configuration."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    return request.app.state.templates.TemplateResponse(
        "admin/settings.html",
        {
            "request": request,
            "current_user": current_user,
            "smtp_host": settings.SMTP_HOST,
            "smtp_port": settings.SMTP_PORT,
            "smtp_user": settings.SMTP_USER,
            "smtp_from": settings.SMTP_FROM,
            "smtp_use_tls": settings.SMTP_USE_TLS,
            "smtp_subject_template": settings.SMTP_SUBJECT_TEMPLATE,
            "smtp_body_template": settings.SMTP_BODY_TEMPLATE,
            "ldap_url": settings.LDAP_URL,
            "ldap_bind_dn": settings.LDAP_BIND_DN,
            "ldap_search_base": settings.LDAP_SEARCH_BASE,
            "ai_enabled": settings.AI_ENABLED,
            "ai_base_url": settings.AI_BASE_URL,
            "ai_model": settings.AI_MODEL,
            # Never render secrets back into the page; only whether one is set.
            "ai_api_key_configured": bool(
                settings.AI_API_KEY and settings.AI_API_KEY != "none"
            ),
            "smtp_password_configured": bool(settings.SMTP_PASSWORD),
        },
    )


@router.post("/settings")
async def update_settings(
    request: Request,
    form_type: str = Form(...),
    smtp_host: str | None = Form(None),
    smtp_port: int | None = Form(None),
    smtp_user: str | None = Form(None),
    smtp_password: str | None = Form(None),
    smtp_from: str | None = Form(None),
    smtp_use_tls: str | None = Form(None),
    smtp_subject_template: str | None = Form(None),
    smtp_body_template: str | None = Form(None),
    ldap_url: str | None = Form(None),
    ldap_bind_dn: str | None = Form(None),
    ldap_search_base: str | None = Form(None),
    ai_enabled: str | None = Form(None),
    ai_base_url: str | None = Form(None),
    ai_api_key: str | None = Form(None),
    ai_model: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Update SMTP, LDAP, or AI settings.

    The three sections post separate forms to this shared handler, so only the
    fields belonging to the submitted ``form_type`` are touched; the others are
    left at their current values instead of being reset to form defaults. Values
    are persisted to the ``app_settings`` table so they survive restarts.
    """
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    updates: dict[str, object] = {}

    if form_type == "smtp":
        updates["SMTP_HOST"] = smtp_host or ""
        if smtp_port is not None:
            updates["SMTP_PORT"] = smtp_port
        updates["SMTP_USER"] = smtp_user or ""
        updates["SMTP_FROM"] = smtp_from or ""
        updates["SMTP_USE_TLS"] = _parse_bool(smtp_use_tls, default=True)
        updates["SMTP_SUBJECT_TEMPLATE"] = smtp_subject_template or ""
        updates["SMTP_BODY_TEMPLATE"] = smtp_body_template or ""
        # Blank password means "leave the stored/configured one unchanged".
        if smtp_password:
            updates["SMTP_PASSWORD"] = smtp_password
    elif form_type == "ldap":
        updates["LDAP_URL"] = ldap_url or ""
        updates["LDAP_BIND_DN"] = ldap_bind_dn or ""
        updates["LDAP_SEARCH_BASE"] = ldap_search_base or ""
    elif form_type == "ai":
        updates["AI_ENABLED"] = _parse_bool(ai_enabled, default=False)
        updates["AI_BASE_URL"] = ai_base_url or ""
        updates["AI_MODEL"] = ai_model or ""
        if ai_api_key:
            updates["AI_API_KEY"] = ai_api_key
    else:
        raise HTTPException(status_code=400, detail="Unknown settings form")

    for key, value in updates.items():
        setattr(settings, key, value)
    await save_setting_updates(db, updates)

    return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/test-email")
async def test_email(
    request: Request,
    to_email: str = Form("test@example.com"),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Send a test email using current SMTP settings."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        # Render templates with sample data
        subject = settings.SMTP_SUBJECT_TEMPLATE.replace("{{report_name}}", "Test Report")
        body = settings.SMTP_BODY_TEMPLATE.replace("{{report_name}}", "Test Report").replace("{{generated_at}}", "2026-08-27 12:00:00")

        success = await send_email(
            smtp_host=settings.SMTP_HOST,
            smtp_port=settings.SMTP_PORT,
            smtp_user=settings.SMTP_USER,
            smtp_password=settings.SMTP_PASSWORD,
            smtp_from=settings.SMTP_FROM,
            to_emails=[to_email],
            subject=subject,
            body=body,
        )

        if success:
            return {"status": "ok", "message": "Test email sent successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send test email")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email test failed: {e!s}")
