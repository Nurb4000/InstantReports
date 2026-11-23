from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.user import now_utc


class PendingSession(Base):
    """Server-side record for the second step of a two-step login.

    When a local account has MFA enabled, the password is verified in the first
    ``/auth/login`` step and the browser is sent to ``/auth/mfa-verify``. To
    keep that second step stateless-safe (a random cookie alone could be replayed
    by anyone who obtains it), we persist a short-lived record here keyed by a
    signed token stored in the cookie. The record ties the pending login to a
    specific user and expires quickly, so a stolen pending cookie is useless for
    long and cannot be reused once the full session has been issued or it lapses.

    Mirrors :class:`app.models.revoked_token.RevokedToken`: a server-side table
    of opaque tokens with an expiry, used to gate an otherwise stateless flow.
    """

    __tablename__ = "pending_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
