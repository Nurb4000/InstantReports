from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.revoked_token import RevokedToken
from app.models.user import AuthSource, User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def escape_ldap_filter(value: str) -> str:
    """Escape LDAP search-filter metacharacters per RFC 4515.

    Login input (email/username) is embedded in an LDAP search filter; without
    escaping, characters like ``*`` ``(`` ``)`` and ``\\`` let an attacker
    restructure the filter (LDAP injection, CWE-546) to enumerate users or
    manipulate results. The NUL byte is also escaped since it terminates the
    filter string in many LDAP server implementations.
    """
    return (
        value.replace("\\", "\\5C")
        .replace("*", "\\2A")
        .replace("(", "\\28")
        .replace(")", "\\29")
        .replace("\x00", "\\00")
    )


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    # A unique token id so the JWT can be revoked server-side (e.g. on logout).
    to_encode.update({"jti": secrets.token_hex(16), "exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


def _to_aware_datetime(value: Any) -> datetime:
    """Coerce a JWT ``exp`` (unix seconds) or datetime into an aware datetime.

    ``jose`` decodes ``exp`` to an integer unix timestamp; the DB DateTime column
    needs a datetime object, so convert here to keep callers simple and safe.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    raise TypeError(f"Cannot interpret {value!r} as an expiry datetime")


async def revoke_access_token(
    db: AsyncSession, jti: str, sub: str | None, expires_at: datetime
) -> None:
    """Record ``jti`` as revoked until ``expires_at`` so the JWT cannot be used again.

    Idempotent: a unique constraint on ``jti`` means re-revoking an already
    revoked token is a harmless no-op (the insert is rolled back).
    """
    try:
        db.add(RevokedToken(jti=jti, sub=sub, expires_at=_to_aware_datetime(expires_at)))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Failed to revoke token %s", jti, exc_info=True)


async def is_token_revoked(db: AsyncSession, jti: str | None) -> bool:
    """Return True if ``jti`` has been revoked and the token has not yet expired."""
    if not jti:
        return False
    result = await db.execute(
        select(RevokedToken.id).where(
            RevokedToken.jti == jti,
            RevokedToken.expires_at > datetime.now(timezone.utc),
        )
    )
    return result.scalar_one_or_none() is not None


async def get_current_user(
    token: str | None = None,
    db: AsyncSession | None = None,
) -> User | None:
    if not token or not db:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    # Reject tokens that have been revoked since issuance (e.g. after logout).
    if await is_token_revoked(db, payload.get("jti")):
        return None

    email: str | None = payload.get("sub")
    if not email:
        return None

    result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or user.auth_source != AuthSource.LOCAL:
        return None

    if not verify_password(password, user.password_hash or ""):
        return None

    return user


class LDAPConfigError(Exception):
    """LDAP backend is unreachable or misconfigured.

    Raised by :func:`authenticate_ldap_user` when connecting to, or binding
    with the service account on, the LDAP server fails (a transport/config
    error) -- as opposed to a legitimate "unknown user / wrong password"
    authentication failure, which returns ``None``. Distinguishing the two lets
    the login path log backend problems for debugging while still showing users
    the uniform "incorrect email or password" response, so a broken backend does
    not masquerade as per-user credential failures (which both hides
    misconfiguration and aids user enumeration).
    """


async def authenticate_ldap_user(db: AsyncSession, email: str, password: str) -> User | None:
    if not settings.LDAP_URL or not settings.LDAP_SEARCH_BASE:
        return None

    import ldap3
    from ldap3.exceptions import LDAPException

    # --- transport/config phase: create the server and bind with the service
    # account. Any failure here means the backend is unreachable or misconfigured
    # (we have not yet looked up this user), so surface it distinctly instead of
    # swallowing it into a generic "wrong password".
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
    except Exception as exc:  # noqa: BLE001 - any error here is a backend/config failure
        logger.error(
            "LDAP backend unreachable or misconfigured at %s: %s",
            settings.LDAP_URL, exc, exc_info=True,
        )
        raise LDAPConfigError(str(exc)) from exc

    try:
        search_filter = f"({settings.LDAP_USER_ATTR}={escape_ldap_filter(email)})"
        conn.search(
            search_base=settings.LDAP_SEARCH_BASE,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=[settings.LDAP_EMAIL_ATTR, settings.LDAP_NAME_ATTR, "dn"],
        )

        if not conn.entries:
            return None  # unknown user -> normal authentication failure

        entry = conn.entries[0]
        user_dn = str(entry.entry_dn)

        user_conn = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
        )

        if not user_conn.bound:
            return None  # wrong password -> normal authentication failure

        email_attr = settings.LDAP_EMAIL_ATTR.lower()
        name_attr = settings.LDAP_NAME_ATTR.lower()

        ldap_email = str(entry[email_attr].value).lower() if email_attr in entry.entry_attributes else ""
        ldap_name = str(entry[name_attr].value) if name_attr in entry.entry_attributes else ""

        user_conn.unbind()

        # Reuse an existing LDAP-backed local row.
        result = await db.execute(select(User).where(User.email == ldap_email))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            if existing_user.auth_source == AuthSource.LDAP:
                return existing_user
            # Email already owned by a LOCAL user; the LDAP login cannot apply.
            return None

        # Provision a new local row for the newly-seen LDAP user. The unique
        # email column means a concurrent login can race us to the insert; if
        # that happens, roll back and return the existing row so a valid login is
        # not swallowed into "incorrect email or password".
        try:
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
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(User).where(User.email == ldap_email))
            return result.scalar_one_or_none()

    except LDAPException as exc:
        # Unexpected LDAP error during search/user-bind (a bad password is
        # handled above via the bound check). Log distinctly rather than
        # swallowing it into a uniform response.
        logger.warning("LDAP authentication error for %s: %s", email, exc)
        return None


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
