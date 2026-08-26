from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import APIKey


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
        expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

    api_key = APIKey(
        key=key,
        name=name,
        user_id=user_id,
        permissions=",".join(permissions or ["read"]),
        expires_at=expires_at,
    )
    
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key


async def validate_api_key(db: AsyncSession, key: str) -> APIKey | None:
    """Validate an API key and return the key object if valid.

    Args:
        db: Database session
        key: The API key to validate

    Returns:
        APIKey if valid, None otherwise
    """
    result = await db.execute(select(APIKey).where(APIKey.key == key))
    api_key = result.scalar_one_or_none()

    if not api_key:
        return None

    if not api_key.is_active:
        return None

    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        return None

    # Update last used timestamp
    api_key.last_used_at = datetime.utcnow()
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
