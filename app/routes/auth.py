from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
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

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


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
        result = await db.execute(__import__("sqlalchemy").select(User).where(User.email == email, User.is_active == True))
        user = result.scalar_one_or_none()
        return user
    except JWTError:
        return None


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> Any:
    user = await authenticate_user(db, email, password)

    if not user:
        user = await authenticate_ldap_user(db, email, password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.email})

    redirect_url = "/designer" if settings.MODE == "designer" else "/portal"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=60 * 60 * 8,
        samesite="lax",
    )
    # Set current user ID cookie for owner selection
    response.set_cookie(
        key="current_user_id",
        value=str(user.id),
        httponly=False,
        max_age=60 * 60 * 8,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key="access_token")
    return response
