from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import AuthSource, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or user.auth_source != AuthSource.LOCAL:
        return None

    if not verify_password(password, user.password_hash or ""):
        return None

    return user


async def authenticate_ldap_user(db: AsyncSession, email: str, password: str) -> User | None:
    if not settings.LDAP_URL or not settings.LDAP_SEARCH_BASE:
        return None

    import ldap3

    try:
        server = ldap3.Server(
            settings.LDAP_URL,
            get_info=ldap3.ALL,
            connect_timeout=5,
        )
        conn = ldap3.Connection(
            server,
            user=f"{settings.LDAP_BIND_DN}",
            password=settings.LDAP_BIND_PASSWORD,
            auto_bind=True,
        )

        search_filter = f"({settings.LDAP_USER_ATTR}={email})"
        conn.search(
            search_base=settings.LDAP_SEARCH_BASE,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=[settings.LDAP_EMAIL_ATTR, settings.LDAP_NAME_ATTR, "dn"],
        )

        if not conn.entries:
            return None

        entry = conn.entries[0]
        user_dn = str(entry.entry_dn)

        user_conn = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
        )

        if not user_conn.bound:
            return None

        email_attr = settings.LDAP_EMAIL_ATTR.lower()
        name_attr = settings.LDAP_NAME_ATTR.lower()

        ldap_email = str(entry[email_attr].value).lower() if email_attr in entry.entry_attributes else ""
        ldap_name = str(entry[name_attr].value) if name_attr in entry.entry_attributes else ""

        user_conn.unbind()

        result = await db.execute(select(User).where(User.email == ldap_email))
        existing_user = result.scalar_one_or_none()

        if existing_user:
            if existing_user.auth_source != AuthSource.LDAP:
                return None
            return existing_user

        new_user = User(
            email=ldap_email,
            name=ldap_name,
            role="viewer",
            auth_source=AuthSource.LDAP,
            ldap_dn=user_dn,
            is_active=True,
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user

    except Exception:
        return None


async def get_current_user(
    token: str | None = None,
    db: AsyncSession | None = None,
) -> User | None:
    if not token or not db:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    email: str | None = payload.get("sub")
    if not email:
        return None

    result = await db.execute(select(User).where(User.email == email, User.is_active == True))
    user = result.scalar_one_or_none()
    return user


async def create_local_user(
    db: AsyncSession,
    email: str,
    name: str,
    password: str,
    role: str = "viewer",
) -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=role,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
