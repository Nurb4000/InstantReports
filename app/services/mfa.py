"""MFA orchestration for optional TOTP on local accounts (Gap #10).

Builds on :mod:`app.services.totp`. The design keeps MFA strictly opt-in:
accounts start with ``mfa_enabled=False`` and only take effect when a user
enables them; org-wide enforcement is handled by ``settings.MFA_REQUIRED`` in
the login route, not here.

Flow summary:
- ``prepare_enable`` generates a TOTP secret + backup codes and persists the
  secret (leaving ``mfa_enabled`` False) so it can be verified on confirm.
- ``confirm_enable`` validates one authenticator code and flips ``mfa_enabled``.
- ``verify_login_code`` is the login second step: a valid TOTP code, or a
  single-use backup code as fallback if the device is unavailable.
- ``disable_mfa`` / ``admin_reset_mfa`` clear the secret, codes, and flag.
- ``rotate_backup_codes`` revokes the current set and issues fresh ones.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.services import totp

_alphabet = string.ascii_letters + string.digits
BACKUP_CODE_LENGTH = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _random_backup_code() -> str:
    return "".join(secrets.choice(_alphabet) for _ in range(BACKUP_CODE_LENGTH))


def _hash_backup_code(code: str) -> str:
    """Salted SHA-256 of a backup code, stored as ``salt$digest``."""
    salt = secrets.token_hex(8)
    digest = hashlib.sha256((salt + code).encode()).hexdigest()
    return f"{salt}${digest}"


def _verify_backup_code(stored: str, code: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.sha256((salt + code).encode()).hexdigest()
    return secrets.compare_digest(candidate, digest)


async def prepare_enable(db: AsyncSession, user: User) -> tuple[str, list[str]]:
    """Generate a TOTP secret + backup codes and persist them.

    The secret is stored but ``mfa_enabled`` stays False until
    :func:`confirm_enable` succeeds, so an abandoned setup leaves a harmless,
    unused secret. Returns ``(secret, backup_codes)`` for QR display.
    """
    secret = totp.generate_secret()
    codes = [_random_backup_code() for _ in range(settings.MFA_BACKUP_CODES_COUNT)]
    user.mfa_secret = secret
    for code in codes:
        db.add(UserBackupCode(user_id=user.id, code_hash=_hash_backup_code(code)))
    await db.commit()
    await db.refresh(user)
    return secret, codes


async def confirm_enable(db: AsyncSession, user: User, code: str, now: float | None = None) -> bool:
    """Validate one authenticator code against the stored secret and enable MFA."""
    if not user.mfa_secret:
        return False
    if totp.verify_code(user.mfa_secret, code, now=now):
        user.mfa_enabled = True
        await db.commit()
        await db.refresh(user)
        return True
    return False


async def verify_login_code(
    db: AsyncSession,
    user: User,
    code: str,
    now: float | None = None,
) -> tuple[bool, bool]:
    """Second login step. Returns ``(ok, used_backup_code)``.

    Tries the TOTP code first; if it fails and the user has an unused backup
    code matching ``code``, consumes that instead. A consumed backup code still
    yields ``ok=True`` but signals ``used_backup_code=True`` so callers can warn
    the user to rotate their codes.
    """
    if not user.mfa_enabled:
        return False, False

    if user.mfa_secret and totp.verify_code(user.mfa_secret, code, now=now):
        return True, False

    if not code:
        return False, False

    result = await db.execute(
        select(UserBackupCode).where(
            UserBackupCode.user_id == user.id,
            UserBackupCode.used_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    for backup in candidates:
        if _verify_backup_code(backup.code_hash, code):
            backup.used_at = _now()
            await db.commit()
            return True, True

    return False, False


async def disable_mfa(db: AsyncSession, user: User, password: str) -> bool:
    """Verify the password, then clear the secret, codes, and flag."""
    from app.auth import verify_password

    if not user.password_hash or not verify_password(password, user.password_hash):
        return False

    user.mfa_secret = None
    user.mfa_enabled = False
    result = await db.execute(
        select(UserBackupCode).where(UserBackupCode.user_id == user.id)
    )
    for backup in list(result.scalars().all()):
        await db.delete(backup)
    return True


async def rotate_backup_codes(db: AsyncSession, user: User) -> list[str]:
    """Revoke all existing backup codes and issue a fresh set."""
    result = await db.execute(
        select(UserBackupCode).where(UserBackupCode.user_id == user.id)
    )
    for backup in list(result.scalars().all()):
        await db.delete(backup)

    codes = [_random_backup_code() for _ in range(settings.MFA_BACKUP_CODES_COUNT)]
    for code in codes:
        db.add(UserBackupCode(user_id=user.id, code_hash=_hash_backup_code(code)))
    await db.commit()
    return codes


async def admin_reset_mfa(db: AsyncSession, user: User) -> None:
    """Admin recovery path: clear the secret, codes, and flag (no password)."""
    user.mfa_secret = None
    user.mfa_enabled = False
    result = await db.execute(
        select(UserBackupCode).where(UserBackupCode.user_id == user.id)
    )
    for backup in list(result.scalars().all()):
        await db.delete(backup)
    await db.commit()
