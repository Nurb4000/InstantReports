"""Rotation of connection credentials with a grace period (Gap #11).

Connection secrets live in plaintext inside ``DataConnection.config`` (JSONB).
Rotating one in place would break scheduled reports / webhooks that still hold
the retired value. These helpers retain the retired value -- hashed for
at-rest protection with the plaintext kept for in-grace resolution -- so it
keeps working for a grace window, then is pruned.

The resolution layer (``resolve_connection_config``) re-exposes in-grace values
under ``_previous_<field>`` keys, and ``connector_secret`` lets connectors fall
back to a still-valid previous value when the current one is absent.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import ConnectionCredentialRotation, DataConnection

# Default / maximum grace periods (days) for a retired connection credential
# (Gap #11). Mirrors the API-key rotation caps so the two mechanisms agree.
DEFAULT_CREDENTIAL_GRACE_PERIOD_DAYS = 7
MAX_CREDENTIAL_GRACE_PERIOD_DAYS = 30

# Suffix under which a retired credential is re-exposed by
# ``resolve_connection_config`` so connectors can fall back to it during grace.
_PREVIOUS_KEY_SUFFIX = "_previous_"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hash_secret(secret: str) -> str:
    """SHA-256 hex digest of a credential, stored for at-rest identification.

    The plaintext is retained separately (``secret_value``) so it can be used
    during the grace window; the hash lets us recognise a retired value without
    exposing it in indexes or logs.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


async def _load_owned_connection(db: AsyncSession, connection_id: Any, user_id: Any) -> DataConnection:
    result = await db.execute(
        select(DataConnection).where(
            DataConnection.id == connection_id, DataConnection.created_by == user_id
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise KeyError(
            f"Connection {connection_id} not found or not owned by user {user_id}"
        )
    return connection


async def rotate_connection_secret(
    db: AsyncSession,
    connection_id: Any,
    user_id: Any,
    field_name: str,
    new_secret: str,
    grace_period_days: int = DEFAULT_CREDENTIAL_GRACE_PERIOD_DAYS,
) -> DataConnection:
    """Rotate a single credential field on a connection (Gap #11).

    The retired value is retained in ``connection_credential_rotations`` with a
    ``valid_until`` of now + grace_period_days so scheduled reports / webhooks
    that still hold it keep working until the grace window closes. The
    connection's ``config[field_name]`` is updated to ``new_secret`` and expired
    rotations are pruned.

    Args:
        db: Database session
        connection_id: Connection whose credential is being rotated
        user_id: User performing the rotation (must own the connection)
        field_name: Config key holding the secret (e.g. "password", "auth_token")
        new_secret: The new secret value
        grace_period_days: Days the retired value stays valid (1..MAX)

    Returns:
        The updated DataConnection (its ``config`` holds the new secret).

    Raises:
        KeyError: if no such connection exists or the caller does not own it.
        ValueError: if grace_period_days is out of range.
    """
    if not (1 <= grace_period_days <= MAX_CREDENTIAL_GRACE_PERIOD_DAYS):
        raise ValueError(
            f"grace_period_days must be between 1 and {MAX_CREDENTIAL_GRACE_PERIOD_DAYS}"
        )

    connection = await _load_owned_connection(db, connection_id, user_id)

    old_value = connection.config.get(field_name)
    # Only retain the retired value when it actually changes; nothing to grace
    # if the field was absent or already equals the new secret.
    if old_value is not None and old_value != new_secret:
        valid_until = now_utc() + timedelta(days=grace_period_days)
        db.add(
            ConnectionCredentialRotation(
                connection_id=connection.id,
                field_name=field_name,
                secret_hash=hash_secret(old_value),
                secret_value=old_value,
                valid_until=valid_until,
            )
        )

    # Reassign the whole dict rather than mutating it in place: SQLAlchemy does
    # not detect in-place mutations of a plain JSONB column, so the change would
    # silently fail to flush. Building a new dict guarantees the dirty flag is set.
    new_config = dict(connection.config)
    new_config[field_name] = new_secret
    connection.config = new_config
    await prune_expired_connection_rotations(db, connection.id)
    await db.commit()
    await db.refresh(connection)
    return connection


async def resolve_connection_config(
    db: AsyncSession,
    connection: DataConnection,
) -> dict[str, Any]:
    """Return a config dict with current secrets plus in-grace previous ones.

    Retired values still within their grace window are re-exposed under
    ``_previous_<field>`` keys so connectors can fall back to them. Expired
    rotations are pruned as a side effect. The connection's own ``config`` is
    left untouched; callers should use this resolved copy for credential reads.
    """
    now = now_utc()
    result = await db.execute(
        select(ConnectionCredentialRotation).where(
            ConnectionCredentialRotation.connection_id == connection.id,
            ConnectionCredentialRotation.valid_until > now,
        )
    )
    rotations = list(result.scalars().all())

    resolved = dict(connection.config)
    for rotation in rotations:
        resolved[f"{_PREVIOUS_KEY_SUFFIX}{rotation.field_name}"] = rotation.secret_value

    # Expired entries are no longer usable; drop them to keep the table tidy.
    # Prune unconditionally -- the in-grace query above excludes expired rows, so
    # guarding on ``rotations`` would skip cleanup exactly when it is needed.
    await prune_expired_connection_rotations(db, connection.id)

    return resolved


async def prune_expired_connection_rotations(db: AsyncSession, connection_id: Any) -> int:
    """Delete rotated credentials whose grace period has elapsed.

    Callers must commit for the deletions to take effect. Returns the number of
    rows pruned.
    """
    now = now_utc()
    result = await db.execute(
        select(ConnectionCredentialRotation).where(
            ConnectionCredentialRotation.connection_id == connection_id,
            ConnectionCredentialRotation.valid_until <= now,
        )
    )
    expired = list(result.scalars().all())
    for row in expired:
        await db.delete(row)
    return len(expired)


def connector_secret(config: dict[str, Any], field: str, default: str = "") -> str:
    """Return the current secret for ``field``, falling back to a still-valid
    previous value (exposed as ``_previous_<field>`` by
    ``resolve_connection_config``) when the current one is absent.

    Connectors should read credentials through this helper rather than
    ``config.get`` so a rotated-out credential keeps working during its grace
    window instead of breaking immediately.
    """
    value = config.get(field)
    if value:
        return value
    previous = config.get(f"{_PREVIOUS_KEY_SUFFIX}{field}")
    return previous if previous else default
