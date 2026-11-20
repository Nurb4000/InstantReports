from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.user import now_utc


class RevokedToken(Base):
    """Server-side record of a JWT that has been revoked (e.g. on logout).

    JWTs are stateless, so without this table a stolen token stays valid until
    it expires. Logging out inserts the token's ``jti`` here; token validation
    rejects any ``jti`` present in this table. ``expires_at`` mirrors the
    original token expiry so a revoked row stops matching once the token would
    have expired anyway, and can be purged afterwards to bound table growth.
    """

    __tablename__ = "revoked_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
