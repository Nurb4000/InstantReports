from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.models.user import User
from app.routes.auth import get_current_user_optional
from app.services.delivery.email import send_email

router = APIRouter()


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
            "mode": settings.MODE,
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
            "ai_api_key": settings.AI_API_KEY,
            "ai_model": settings.AI_MODEL,
        },
    )


@router.post("/settings")
async def update_settings(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form("reports@example.com"),
    smtp_use_tls: bool = Form(True),
    smtp_subject_template: str = Form("Report: {{report_name}}"),
    smtp_body_template: str = Form("Please find the attached report: {{report_name}}\n\nGenerated at: {{generated_at}}"),
    ldap_url: str = Form(""),
    ldap_bind_dn: str = Form(""),
    ldap_search_base: str = Form(""),
    ai_enabled: bool = Form(False),
    ai_base_url: str = Form("http://localhost:8080/v1"),
    ai_api_key: str = Form("none"),
    ai_model: str = Form("local-model"),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Update SMTP, LDAP, and AI settings."""
    if not current_user or get_role_value(current_user) != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    # Update SMTP settings
    settings.SMTP_HOST = smtp_host
    settings.SMTP_PORT = smtp_port
    settings.SMTP_USER = smtp_user
    settings.SMTP_PASSWORD = smtp_password
    settings.SMTP_FROM = smtp_from
    settings.SMTP_USE_TLS = smtp_use_tls
    settings.SMTP_SUBJECT_TEMPLATE = smtp_subject_template
    settings.SMTP_BODY_TEMPLATE = smtp_body_template
    
    # Update LDAP settings
    settings.LDAP_URL = ldap_url
    settings.LDAP_BIND_DN = ldap_bind_dn
    settings.LDAP_SEARCH_BASE = ldap_search_base
    
    # Update AI settings
    settings.AI_ENABLED = ai_enabled
    settings.AI_BASE_URL = ai_base_url
    settings.AI_API_KEY = ai_api_key
    settings.AI_MODEL = ai_model

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


def get_role_value(user):
    """Safely get role value from user (handles Enum or string)."""
    if hasattr(user.role, 'value'):
        return user.role.value
    return user.role
