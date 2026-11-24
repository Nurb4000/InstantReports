"""ORM models for connections, schedules, deliveries and related tables.

.. note:: Encryption decorator footgun (M7)

   ``DataConnection.config`` is a decorated ``EncryptedSecretsJSON`` column, so the
   model's ``__table__.c.config`` is the *decorated* type: raw SQLAlchemy core
   ``select``/``insert``/``update`` via ``DataConnection.__table__`` transparently
   **encrypts on write and decrypts on read**. That is correct for ORM-mapped
   attribute access (the intent), but code that bypasses the ORM to touch the raw
   JSONB bytes -- a re-encryption migration, bulk load, or seeding test -- must use
   :data:`_RAW_CONNECTIONS_TABLE` instead. Using the model table there would
   double-encrypt already-plaintext values or read back ciphertext it cannot
   interpret.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Column, MetaData, Table


def now_utc() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.services.connection_encryption import EncryptedSecretsJSON
from app.services.connection_encryption import EncryptedText


class DataConnection(Base):
    __tablename__ = "data_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(EncryptedSecretsJSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )

    creator: Mapped[User] = relationship(lazy="selectin")


# Raw (undecorated) core Table for ``data_connections`` (M7 footgun escape hatch).
#
# Use this -- never ``DataConnection.__table__`` -- whenever raw SQLAlchemy core
# access needs the *plain* JSONB bytes (e.g. a re-encryption migration or a bulk
# load that already holds plaintext). It is declared against its own ``MetaData``
# so it is NOT part of ``Base.metadata`` (never created/dropped by ``create_all``)
# and its ``config`` column is plain ``JSONB``, so core writes/reads are not
# double-processed by the encryption decorator. Keep its columns in sync with the
# model above.
_RAW_CONNECTIONS_TABLE = Table(
    "data_connections",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(255)),
    Column("connector_type", String(50)),
    Column("config", JSONB()),
    Column("created_by", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)


class ConnectionCredentialRotation(Base):
    """A retired connection credential retained during its rotation grace period.

    When a connection's secret (e.g. ``password``, ``auth_token``) is rotated,
    the retired value is stored here -- hashed (``secret_hash``) for at-rest
    identification and encrypted (``secret_value``) so the plaintext is never on
    disk -- alongside a ``valid_until`` timestamp. ``resolve_connection_config``
    re-exposes in-grace values so scheduled reports / webhooks that still hold
    the old secret keep working until the grace window closes, then they are
    pruned.
    """

    __tablename__ = "connection_credential_rotations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Encrypted at rest (H2); decrypted transparently on read so the in-grace
    # fallback keeps working. The hash lets us recognise a retired value without
    # exposing it in indexes or logs.
    secret_value: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        Index(
            "idx_connection_credential_rotations_lookup",
            "connection_id",
            "field_name",
        ),
    )


class QueryTemplate(Base):
    __tablename__ = "query_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )

    connection: Mapped[DataConnection] = relationship(lazy="selectin")


class QueryHistory(Base):
    __tablename__ = "query_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=True, index=True
    )
    element_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    query_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    report: Mapped[Report | None] = relationship(lazy="selectin")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("reports.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    is_active: Mapped[bool] = mapped_column(default=True)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_format: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pdf, xlsx, csv, html
    delivery_type: Mapped[str] = mapped_column(String(50), default="email")  # email, sftp, smb, webhook
    # Encrypted at rest (H5): delivery secrets (SFTP/SMB password, webhook secret)
    # live in this blob. EncryptedSecretsJSON keeps the compiled type JSONB so no
    # schema change is needed; the ORM decrypts transparently on read and the
    # response layer masks secrets via redact_delivery_config.
    delivery_config: Mapped[dict | None] = mapped_column(EncryptedSecretsJSON, nullable=True)  # {type: email/sftp/smb/webhook, config: {...}}
    recipient_emails: Mapped[str | None] = mapped_column(Text, nullable=True)  # Comma-separated emails for email delivery
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)  # Who owns/sees this in portal
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    report: Mapped[Report] = relationship(back_populates="schedules", lazy="selectin")
    owner: Mapped[User] = relationship(backref="owned_schedules", foreign_keys=[owner_id], lazy="selectin")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    delivery_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Encrypted at rest (H5): mirrors the owning schedule's delivery_config, so
    # SFTP/SMB passwords and webhook secrets stored here are ciphertext on disk.
    # EncryptedSecretsJSON keeps the compiled type JSONB; the ORM decrypts on read.
    config: Mapped[dict] = mapped_column(EncryptedSecretsJSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DeliveryRecipient(Base):
    __tablename__ = "delivery_recipients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    include_in_portal: Mapped[bool] = mapped_column(default=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("reports.id"), nullable=True, index=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("schedules.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("report_outputs.id", ondelete="CASCADE"), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


from app.models.report import Report
