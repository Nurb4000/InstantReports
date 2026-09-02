from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(str, Enum):
    ADMIN = "admin"
    DESIGNER = "designer"
    VIEWER = "viewer"


class AuthSource(str, Enum):
    LOCAL = "local"
    LDAP = "ldap"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(String(50), nullable=False, default=UserRole.VIEWER)
    auth_source: Mapped[AuthSource] = mapped_column(String(50), nullable=False, default=AuthSource.LOCAL)
    ldap_dn: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    reports_created: Mapped[list[Report]] = relationship(back_populates="creator", lazy="selectin")
    versions_created: Mapped[list[ReportVersion]] = relationship(back_populates="author", lazy="selectin")
    tags_created: Mapped[list[ReportTag]] = relationship(back_populates="author", lazy="selectin")
    comments_created: Mapped[list[ReportComment]] = relationship(back_populates="author", lazy="selectin")
