from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import APIKey


def hash_api_key(key: str) -> str:
    """Return the SHA-256 hex digest of an API key.

    Keys are high-entropy tokens (``token_urlsafe(32)`` ~ 256 bits), so an
    unsalted digest is the right primitive: we never need to reverse it, only
    compare lookups, and rainbow tables are irrelevant against that much
    entropy. The hex digest is exactly 64 chars, matching the ``key`` column.
    """
    return hashlib.sha256(key.encode()).hexdigest()


async def generate_api_key(
    db: AsyncSession,
    name: str,
    user_id: uuid.UUID,
    permissions: list[str] | None = None,
    expires_in_days: int | None = None,
) -> APIKey:
    """Generate a new API key.

    Args:
        db: Database session
        name: Human-readable name for the key
        user_id: User who owns the key
        permissions: List of permission strings (e.g., ['read', 'execute'])
        expires_in_days: Number of days until expiration (None for no expiry)

    Returns:
        APIKey with the generated key
    """
    key = f"ir_{secrets.token_urlsafe(32)}"

    expires_at = None
    if expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    # Store only the hash; the plaintext ``key`` is returned to the caller once
    # and never persisted. See ``hash_api_key`` for why an unsalted digest is
    # safe here.
    api_key = APIKey(
        key=hash_api_key(key),
        name=name,
        user_id=user_id,
        permissions=",".join(permissions or ["read"]),
        expires_at=expires_at,
    )

    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key, key


async def validate_api_key(db: AsyncSession, key: str) -> APIKey | None:
    """Validate an API key and return the key object if valid.

    Args:
        db: Database session
        key: The API key to validate

    Returns:
        APIKey if valid, None otherwise
    """
    result = await db.execute(select(APIKey).where(APIKey.key == hash_api_key(key)))
    api_key = result.scalar_one_or_none()

    if not api_key:
        return None

    if not api_key.is_active:
        return None

    expires_at = api_key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        # Some drivers (e.g. SQLite) strip tzinfo on retrieval; normalise to
        # UTC so the comparison below never mixes naive/aware datetimes.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at < datetime.now(timezone.utc):
        return None

    # Update last used timestamp
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return api_key


async def revoke_api_key(db: AsyncSession, key_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Revoke (delete) an API key.

    Args:
        db: Database session
        key_id: ID of the key to revoke
        user_id: ID of the user revoking (must own the key)

    Returns:
        True if deleted, False if not found or not authorized
    """
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user_id)
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        return False

    await db.delete(api_key)
    await db.commit()
    return True


async def list_user_keys(db: AsyncSession, user_id: uuid.UUID) -> list[APIKey]:
    """List all API keys for a user.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        List of APIKey objects (without the actual key value)
    """
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user_id).order_by(APIKey.created_at.desc())
    )
    return list(result.scalars().all())
