from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.user import now_utc


class UserBackupCode(Base):
    """A single-use recovery code for MFA login.

    When a user enables TOTP they also get a small set of backup codes. If they
    lose their authenticator device, one of these lets them complete the second
    login step. Each code is stored only as a salted SHA-256 hash (never in
    cleartext) and is marked ``used_at`` on first successful use so it cannot be
    reused. ``rotate`` revokes the whole set and issues fresh ones.
    """

    __tablename__ = "user_backup_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
