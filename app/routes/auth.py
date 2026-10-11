from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    authenticate_ldap_user,
    authenticate_user,
    create_access_token,
    decode_access_token,
)
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.ratelimit import RateLimiter

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Per-process in-memory rate limiter for login attempts (bypasses weak default
# admins and brute force in typical single-node deployments).
_login_rate_limiter = RateLimiter(
    max_attempts=settings.LOGIN_RATE_LIMIT_ATTEMPTS,
    window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
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
        email: str | None = payload.get("sub")
        if not email:
            return None
        result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
        user = result.scalar_one_or_none()
        return user
    except JWTError:
        return None


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
        user = await authenticate_ldap_user(db, email, password)

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

    access_token = create_access_token(data={"sub": user.email})

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
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key="access_token")
    return response
