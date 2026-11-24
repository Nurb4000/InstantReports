"""encrypt_delivery_credentials_at_rest (H5)

Re-encrypt delivery credentials that were stored in plaintext.

Before this change two JSONB columns held delivery secrets in cleartext:

  * ``schedules.delivery_config`` -- the per-schedule delivery settings, which for
    SFTP/SMB deliveries carry the ``password`` and for webhook deliveries carry the
    ``secret`` (see ``app/services/delivery/config.py::build_delivery_config``), and
  * ``deliveries.config`` -- the normalized copy the scheduler reads to actually
    send a report (mirrors the owning schedule's ``delivery_config``).

Application writes now encrypt these transparently via ``EncryptedSecretsJSON``
(the compiled type is still JSONB, so this is a data-only change -- no schema
alteration). Rows already on disk stay plaintext until this migration
re-encrypts them.

The transform is idempotent: only *plaintext* secret values are encrypted, so a
value that is already ciphertext (or non-secret / empty) is left untouched and
re-running the migration never double-encrypts. Downgrade reverses it by
decrypting with the current key (Fernet is reversible).

Core ``Table`` objects (declared locally, not the ORM model's ``__table__``) are
used so the encryption ``TypeDecorator`` is NOT applied -- the migration reads and
stores exactly the raw bytes rather than having the decorator re-encrypt on write
/ decrypt on read. ``values(config=<dict>)`` on a JSONB column makes asyncpg encode
the blob correctly (same pattern as the H2 connection-credential migration).

Revision ID: f7a2c9d4e8b1
Revises: c2e4f6a8b0d2
Create Date: 2026-11-23 13:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Table, Column, UUID
from sqlalchemy.dialects.postgresql import JSONB

# Revision metadata must be module-level for alembic to read the script.
revision: str = "f7a2c9d4e8b1"
down_revision: str | None = "c2e4f6a8b0d2"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

# Minimal local tables (plain JSONB) for raw byte access -- the ORM encryption
# decorator lives on the mapped model, not here.
_meta = sa.MetaData()
_schedule = Table(
    "schedules",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("delivery_config", JSONB()),
)
_delivery = Table(
    "deliveries",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("config", JSONB()),
)


def _reencrypt(raw):
    from app.services.connection_encryption import reencrypt_config_secrets

    return reencrypt_config_secrets(raw) if raw else raw


def upgrade() -> None:
    conn = op.get_bind()

    # Re-encrypt existing schedules.delivery_config blobs (plaintext secret values).
    for row in conn.execute(sa.select(_schedule.c.id, _schedule.c.delivery_config)).all():
        if row.delivery_config is None:
            continue
        conn.execute(
            sa.update(_schedule)
            .where(_schedule.c.id == row.id)
            .values(delivery_config=_reencrypt(row.delivery_config))
        )

    # Re-encrypt existing deliveries.config blobs.
    for row in conn.execute(sa.select(_delivery.c.id, _delivery.c.config)).all():
        if row.config is None:
            continue
        conn.execute(
            sa.update(_delivery)
            .where(_delivery.c.id == row.id)
            .values(config=_reencrypt(row.config))
        )


def downgrade() -> None:
    from app.services.connection_encryption import decrypt_config_secrets

    conn = op.get_bind()

    for row in conn.execute(sa.select(_schedule.c.id, _schedule.c.delivery_config)).all():
        if row.delivery_config is None:
            continue
        conn.execute(
            sa.update(_schedule)
            .where(_schedule.c.id == row.id)
            .values(delivery_config=decrypt_config_secrets(row.delivery_config))
        )

    for row in conn.execute(sa.select(_delivery.c.id, _delivery.c.config)).all():
        if row.config is None:
            continue
        conn.execute(
            sa.update(_delivery)
            .where(_delivery.c.id == row.id)
            .values(config=decrypt_config_secrets(row.config))
        )
