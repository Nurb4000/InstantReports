from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import APIKey, APIKeyRotation

# Default and maximum grace periods (in days) during which a retired API key
# secret remains valid after rotation (Gap #11). The default is generous enough
# for clients to pick up the new secret; the cap prevents a rotated secret from
# lingering effectively forever.
DEFAULT_ROTATION_GRACE_PERIOD_DAYS = 7
MAX_ROTATION_GRACE_PERIOD_DAYS = 30


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
) -> tuple[APIKey, str]:
    """Generate a new API key.

    Args:
        db: Database session
        name: Human-readable name for the key
        user_id: User who owns the key
        permissions: List of permission strings (e.g., ['read', 'execute'])
        expires_in_days: Number of days until expiration (None for no expiry)

    Returns:
        Tuple of (persisted APIKey model, raw plaintext key string). The raw
        key is shown to the user once and never persisted.
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
    """Validate an API key, accepting either the current secret or a retired
    secret still within its rotation grace period (Gap #11).

    Args:
        db: Database session
        key: The API key to validate

    Returns:
        APIKey if valid (current or in-grace rotated), None otherwise
    """
    api_key = await _resolve_validated_key(db, key)
    if api_key is None:
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

    # Update last used timestamp.  Use ``expire_on_commit=False``-style lazy
    # flush: mark the attribute and let the caller's session commit it later,
    # avoiding a synchronous ``COMMIT`` on every API-key-authenticated request.
    api_key.last_used_at = datetime.now(timezone.utc)

    return api_key


async def _resolve_validated_key(db: AsyncSession, key: str) -> APIKey | None:
    """Return the APIKey whose current or in-grace rotated secret matches ``key``.

    Fast path: the presented hash matches the active ``APIKey.key``.
    Grace-period path: the presented hash matches a retired secret stored in
    ``api_key_rotations`` whose ``valid_until`` is still in the future.
    """
    key_hash = hash_api_key(key)

    result = await db.execute(select(APIKey).where(APIKey.key == key_hash))
    api_key = result.scalar_one_or_none()
    if api_key is not None:
        return api_key

    # Not the current secret -- check whether it is a retired secret still in
    # its grace period.
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(APIKeyRotation)
        .where(APIKeyRotation.key_hash == key_hash, APIKeyRotation.valid_until > now)
        .order_by(APIKeyRotation.created_at.desc())
    )
    rotation = result.scalar_one_or_none()
    if rotation is None:
        return None

    result = await db.execute(select(APIKey).where(APIKey.id == rotation.api_key_id))
    return result.scalar_one_or_none()


async def rotate_api_key(
    db: AsyncSession,
    key_id: uuid.UUID,
    user_id: uuid.UUID,
    grace_period_days: int = DEFAULT_ROTATION_GRACE_PERIOD_DAYS,
) -> tuple[APIKey, str]:
    """Rotate an API key while keeping the retired secret valid for a grace period.

    A new secret is generated and becomes the active key immediately. The retired
    secret's hash is retained in ``api_key_rotations`` with a ``valid_until`` of
    now + grace_period_days, so clients that have not yet updated their credential
    keep working until the grace window closes instead of breaking on rotation.
    Expired rotations are pruned as part of the operation.

    Args:
        db: Database session
        key_id: ID of the API key to rotate
        user_id: ID of the user performing the rotation (must own the key)
        grace_period_days: Days the retired secret stays valid (1..MAX)

    Returns:
        Tuple of (updated APIKey model, new plaintext key string). The raw key is
        shown to the caller once and never persisted.

    Raises:
        KeyError: if no such key exists or the caller does not own it.
        ValueError: if grace_period_days is out of the supported range.
    """
    if grace_period_days < 1:
        raise ValueError("grace_period_days must be >= 1")
    if grace_period_days > MAX_ROTATION_GRACE_PERIOD_DAYS:
        grace_period_days = MAX_ROTATION_GRACE_PERIOD_DAYS

    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user_id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise KeyError(f"API key {key_id} not found or not owned by user {user_id}")

    # Retain the current secret as a rotated (grace-period) entry before replacing
    # it, so the retired secret keeps validating for the full grace window.
    if api_key.key:
        valid_until = datetime.now(timezone.utc) + timedelta(days=grace_period_days)
        db.add(
            APIKeyRotation(
                api_key_id=api_key.id,
                key_hash=api_key.key,
                valid_until=valid_until,
            )
        )

    # Mint the new secret. Only its hash is persisted; plaintext is returned once.
    new_key = f"ir_{secrets.token_urlsafe(32)}"
    api_key.key = hash_api_key(new_key)

    await prune_expired_rotations(db, api_key.id)
    await db.commit()
    await db.refresh(api_key)
    return api_key, new_key


async def prune_expired_rotations(db: AsyncSession, key_id: uuid.UUID) -> int:
    """Delete rotated secrets whose grace period has elapsed.

    Callers must commit the session for the deletions to take effect. Returns the
    number of rows pruned.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(APIKeyRotation).where(
            APIKeyRotation.api_key_id == key_id, APIKeyRotation.valid_until <= now
        )
    )
    expired = list(result.scalars().all())
    for row in expired:
        await db.delete(row)
    return len(expired)


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
