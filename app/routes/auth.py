from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    LDAPConfigError,
    authenticate_ldap_user,
    authenticate_user,
    create_access_token,
    decode_access_token,
    is_token_revoked,
    revoke_access_token,
)
from app.config import settings
from app.database import get_db
from app.models.user import AuthSource, User
from app.services import mfa, pending_session, totp
from app.services.ratelimit import build_rate_limiter

router = APIRouter()
logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Login-attempt rate limiter (bypasses weak default admins and brute force in
# typical single-node deployments). Built through the factory so a multi-worker
# deployment can opt into a shared, restart-surviving store via
# RATE_LIMIT_BACKEND=file; defaults to the historical in-process limiter.
_login_rate_limiter = build_rate_limiter(
    settings.LOGIN_RATE_LIMIT_ATTEMPTS,
    settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)

# Rate-limits the MFA verification step so a stolen pending cookie cannot be
# brute-forced at 6 digits. Keyed by client + user email to bound abuse while
# not blocking unrelated users sharing an IP.
_mfa_verify_rate_limiter = build_rate_limiter(
    settings.MFA_VERIFY_RATE_LIMIT_ATTEMPTS,
    settings.MFA_VERIFY_RATE_LIMIT_WINDOW_SECONDS,
)


def _client_key(request: Request) -> str:
    # Prefer real IP from proxy headers if present, fall back to client host.
    forward = request.headers.get("x-forwarded-for")
    if forward:
        return forward.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def get_current_user_optional(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not token:
        cookie_token = request.cookies.get("access_token")
        if cookie_token:
            token = cookie_token

    if not token:
        return None

    try:
        payload = decode_access_token(token)
        if payload is None:
            return None
        # Reject tokens revoked since issuance (e.g. the user logged out).
        if await is_token_revoked(db, payload.get("jti")):
            return None
        email: str | None = payload.get("sub")
        if not email:
            return None
        result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
        user = result.scalar_one_or_none()
        return user
    except JWTError:
        return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login form.

    Several routes (e.g. version compare) redirect to ``/auth/login`` on 401;
    without this handler the browser sees a 405 Method Not Allowed.
    """
    return request.app.state.templates.TemplateResponse(
        "login.html", {"request": request}
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Rate-limit by client and target email to blunt brute force/credential stuffing.
    key = f"{_client_key(request)}:{email.lower()}"
    retry = _login_rate_limiter.retry_after(key)
    if retry > 0:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many login attempts. Please try again later."},
            headers={"Retry-After": str(retry)},
        )

    user = await authenticate_user(db, email, password)

    if not user:
        try:
            user = await authenticate_ldap_user(db, email, password)
        except LDAPConfigError:
            # Backend down/misconfigured (logged at error level inside
            # authenticate_ldap_user). Keep the uniform "incorrect email or
            # password" response to resist enumeration; the failed attempt is
            # rate-limited below so a broken backend cannot be hammered.
            pass

    if not user:
        _login_rate_limiter.record(key)
        # Render login page with error message instead of JSON
        return request.app.state.templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Incorrect email or password",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Successful login resets the rate-limit window for this key (prevents
    # a lucky guess immediately followed by a tight retry block).
    _login_rate_limiter.reset(key)

    # Two-step MFA for local accounts. This is opt-in per user (mfa_enabled);
    # when off, or for LDAP accounts, login completes in this single step below.
    if user.auth_source == AuthSource.LOCAL and user.mfa_enabled:
        token = await pending_session.issue_pending_session(db, user.email)
        response = RedirectResponse(
            url="/auth/mfa-verify", status_code=status.HTTP_302_FOUND
        )
        response.set_cookie(
            key="mfa_pending_token",
            value=token,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            max_age=settings.MFA_PENDING_TTL_SECONDS,
            samesite="lax",
        )
        return response

    # Org-wide enforcement (still off by default): a LOCAL account without MFA
    # enabled cannot finish logging in until it sets up MFA.
    if settings.MFA_REQUIRED and user.auth_source == AuthSource.LOCAL and not user.mfa_enabled:
        return RedirectResponse(
            url="/auth/mfa-setup", status_code=status.HTTP_302_FOUND
        )

    access_token = create_access_token(data={"sub": user.email})

    # Route viewers to the portal; everyone else goes to the designer.
    from app.models.user import UserRole

    if user.role == UserRole.VIEWER:
        redirect_url = "/portal"
    else:
        redirect_url = "/designer"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        max_age=60 * 60 * 8,
        samesite="lax",
    )
    # Set current user ID cookie for owner selection
    response.set_cookie(
        key="current_user_id",
        value=str(user.id),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        max_age=60 * 60 * 8,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Revoke the current JWT so a leaked/stolen token cannot be reused after
    # logout (JWTs are otherwise valid until their ``exp``). Best-effort: a
    # failure to revoke only leaves the token live until it naturally expires.
    auth_header = request.headers.get("Authorization", "")
    token = request.cookies.get("access_token") or (
        auth_header.removeprefix("Bearer ") if auth_header else None
    )
    if token:
        try:
            payload = decode_access_token(token)
            if payload:
                await revoke_access_token(
                    db, payload.get("jti"), payload.get("sub"), payload.get("exp")
                )
        except Exception:
            logger.warning("Failed to revoke token on logout", exc_info=True)

    response.delete_cookie(key="access_token")
    response.delete_cookie(key="current_user_id")
    return response


# ---------------------------------------------------------------------------
# Optional MFA (Gap #10)
# ---------------------------------------------------------------------------


def _login_redirect_for(user: User) -> str:
    """Route viewers to the portal; everyone else goes to the designer."""
    from app.models.user import UserRole

    if user.role == UserRole.VIEWER:
        return "/portal"
    return "/designer"


async def _issue_full_session(
    response: RedirectResponse, user: User
) -> RedirectResponse:
    """Set the full session cookies after MFA has been verified."""
    access_token = create_access_token(data={"sub": user.email})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        max_age=60 * 60 * 8,
        samesite="lax",
    )
    response.set_cookie(
        key="current_user_id",
        value=str(user.id),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        max_age=60 * 60 * 8,
        samesite="lax",
    )
    return response


@router.get("/mfa-verify", response_class=HTMLResponse)
async def mfa_verify_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the second-step MFA form.

    Reached after the password is verified in ``/auth/login`` for an MFA-enabled
    local account. The pending-session cookie (set by that step) authorises this
    page; without it the user is sent back to login.
    """
    token = request.cookies.get("mfa_pending_token")
    if not token:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    user_email = await pending_session.get_pending_user_email(db, token)
    if not user_email:
        return request.app.state.templates.TemplateResponse(
            "mfa_verify.html",
            {"request": request, "error": "Verification session expired. Please log in again."},
        )

    return request.app.state.templates.TemplateResponse(
        "mfa_verify.html", {"request": request}
    )


@router.post("/mfa-verify", response_class=HTMLResponse)
async def mfa_verify(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Validate the TOTP code (or a single-use backup code) and finish login."""
    token = request.cookies.get("mfa_pending_token")
    if not token:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    # Consume the pending session first so a stolen cookie cannot be replayed.
    user_email = await pending_session.consume_pending_session(db, token)
    if not user_email:
        return request.app.state.templates.TemplateResponse(
            "mfa_verify.html",
            {"request": request, "error": "Verification session expired. Please log in again."},
        )

    result = await db.execute(select(User).where(User.email == user_email))
    user = result.scalar_one_or_none()
    if not user:
        return request.app.state.templates.TemplateResponse(
            "mfa_verify.html",
            {"request": request, "error": "Verification session expired. Please log in again."},
        )

    key = f"{_client_key(request)}:{user_email.lower()}"
    retry = _mfa_verify_rate_limiter.retry_after(key)
    if retry > 0:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many attempts. Please try again later."},
            headers={"Retry-After": str(retry)},
        )

    ok, used_backup = await mfa.verify_login_code(db, user, code)
    if not ok:
        _mfa_verify_rate_limiter.record(key)
        return request.app.state.templates.TemplateResponse(
            "mfa_verify.html",
            {
                "request": request,
                "error": "Incorrect verification code" + (" (backup code)" if used_backup else ""),
                "used_backup": used_backup,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _mfa_verify_rate_limiter.reset(key)
    response = RedirectResponse(
        url=_login_redirect_for(user), status_code=status.HTTP_302_FOUND
    )
    return await _issue_full_session(response, user)


@router.get("/mfa-setup", response_class=HTMLResponse)
async def mfa_setup_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Show the TOTP secret + otpauth:// URI so the user can add it to an app.

    The secret is generated and persisted here (with backup codes) but MFA stays
    disabled until the user confirms a code in ``POST /auth/mfa-setup``. This
    lets them scan/enter the key before committing.
    """
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    secret, backup_codes = await mfa.prepare_enable(db, current_user)
    otp_uri = totp.otpauth_uri(current_user.email, secret, issuer="InstantReports")
    return request.app.state.templates.TemplateResponse(
        "mfa_setup.html",
        {
            "request": request,
            "secret": secret,
            "otp_uri": otp_uri,
            "backup_codes": backup_codes,
        },
    )


@router.post("/mfa-setup")
async def mfa_setup_confirm(
    request: Request,
    code: str = Form(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Confirm MFA setup with the first authenticator code."""
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    ok = await mfa.confirm_enable(db, current_user, code)
    if not ok:
        secret, backup_codes = await mfa.prepare_enable(db, current_user)
        otp_uri = totp.otpauth_uri(current_user.email, secret, issuer="InstantReports")
        return request.app.state.templates.TemplateResponse(
            "mfa_setup.html",
            {
                "request": request,
                "secret": secret,
                "otp_uri": otp_uri,
                "backup_codes": backup_codes,
                "error": "Incorrect verification code. Enter a fresh code from your app.",
            },
        )

    return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)


@router.get("/mfa-disable")
async def mfa_disable_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Prompt for the password before disabling MFA."""
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    return request.app.state.templates.TemplateResponse(
        "mfa_disable.html", {"request": request}
    )


@router.post("/mfa-disable")
async def mfa_disable_confirm(
    request: Request,
    password: str = Form(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Verify the password, then clear MFA for the account."""
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    ok = await mfa.disable_mfa(db, current_user, password)
    if not ok:
        return request.app.state.templates.TemplateResponse(
            "mfa_disable.html",
            {"request": request, "error": "Incorrect password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)


@router.post("/mfa-rotate-backup-codes")
async def mfa_rotate_backup_codes(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current backup codes and issue a fresh set."""
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    codes = await mfa.rotate_backup_codes(db, current_user)
    return request.app.state.templates.TemplateResponse(
        "mfa_backup_codes.html",
        {"request": request, "backup_codes": codes},
    )
