"""Two-step login state for MFA-enabled local accounts (Gap #10).

After the password is verified in the first ``/auth/login`` step, a short-lived
:class:`~app.models.pending_session.PendingSession` is created and its token is
sent to the browser as a cookie. The second step, ``/auth/mfa-verify``, checks
the TOTP code (or a backup code) and, on success, consumes the pending session
and issues the full session cookies.

The pending record makes the second step safe against cookie theft: without it,
anyone who obtained the random cookie could complete login; with it, the token
must match a fresh, user-bound, expiring server-side row.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.pending_session import PendingSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def issue_pending_session(db: AsyncSession, user_email: str) -> str:
    """Create a short-lived pending session and return its token."""
    token = secrets.token_urlsafe(24)
    expires_at = _now() + timedelta(seconds=settings.MFA_PENDING_TTL_SECONDS)
    db.add(PendingSession(token=token, user_email=user_email, expires_at=expires_at))
    await db.commit()
    return token


async def consume_pending_session(db: AsyncSession, token: str) -> str | None:
    """Return the email bound to a valid pending session and delete it.

    A valid token is one that exists and has not yet expired. Consuming it (the
    delete) means the same pending cookie cannot be used twice -- once the full
    session is issued, a replay of ``/auth/mfa-verify`` finds no row. Returns
    ``None`` for an unknown/expired token so callers can treat that as failure.
    """
    result = await db.execute(
        select(PendingSession).where(
            PendingSession.token == token,
            PendingSession.expires_at > _now(),
        )
    )
    pending = result.scalar_one_or_none()
    if pending is None:
        return None

    await db.delete(pending)
    await db.commit()
    return pending.user_email


async def get_pending_user_email(db: AsyncSession, token: str) -> str | None:
    """Return the user email bound to a still-valid pending session, if any."""
    result = await db.execute(
        select(PendingSession.user_email).where(
            PendingSession.token == token,
            PendingSession.expires_at > _now(),
        )
    )
    return result.scalar_one_or_none()
