"""encrypt_connection_credentials_at_rest (H2)

Re-encrypt connection credentials that were stored in plaintext.

Before this change ``DataConnection.config`` (JSONB) held DB passwords / REST
bearer tokens / SMTP creds in cleartext, as did ``connection_credential_rotations
.secret_value``. Application writes now encrypt these transparently via
``EncryptedSecretsJSON`` / ``EncryptedText``, but rows already on disk stay
plaintext until this migration re-encrypts them.

The transform is idempotent: only plaintext secret values are encrypted, so a
value that is already ciphertext (or non-secret / empty) is left untouched and
re-running the migration never double-encrypts. Downgrade reverses it by
decrypting with the current key (Fernet is reversible, unlike the API-key hash).

Core ``Table`` objects (declared locally, not the ORM model's ``__table__``) are
used so the encryption ``TypeDecorator`` is NOT applied -- the migration reads
and stores exactly the raw bytes rather than having the decorator re-encrypt on
write / decrypt on read. ``values(config=<dict>)`` on a JSONB column makes
asyncpg encode the blob correctly.

Revision ID: c2e4f6a8b0d2
Revises: mfacb62fe1e9f46
Create Date: 2026-11-23 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Table, Column, Text, UUID
from sqlalchemy.dialects.postgresql import JSONB

# Revision metadata must be module-level for alembic to read the script.
revision: str = "c2e4f6a8b0d2"
down_revision: str | None = "mfacb62fe1e9f46"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

# Minimal local tables (plain JSONB / Text) for raw byte access -- the ORM
# encryption decorator lives on the mapped model, not here.
_meta = sa.MetaData()
_dc = Table(
    "data_connections",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("config", JSONB()),
)
_rot = Table(
    "connection_credential_rotations",
    _meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("secret_value", Text()),
)


def _reencrypt_config(raw):
    from app.services.connection_encryption import reencrypt_config_secrets

    return reencrypt_config_secrets(raw) if raw else raw


def upgrade() -> None:
    # Lazy import so enumerating revisions (alembic heads/history) does not require
    # the app / SECRET_KEY to be importable.
    from app.services.connection_encryption import reencrypt_secret_value

    conn = op.get_bind()

    # Re-encrypt existing DataConnection.config blobs (plaintext secret values).
    for row in conn.execute(sa.select(_dc.c.id, _dc.c.config)).all():
        if row.config is None:
            continue
        conn.execute(
            sa.update(_dc).where(_dc.c.id == row.id).values(config=_reencrypt_config(row.config))
        )

    # Re-encrypt retained rotation secrets stored in cleartext.
    for row in conn.execute(sa.select(_rot.c.id, _rot.c.secret_value)).all():
        if not row.secret_value:
            continue
        conn.execute(
            sa.update(_rot).where(_rot.c.id == row.id).values(secret_value=reencrypt_secret_value(row.secret_value))
        )


def downgrade() -> None:
    from app.services.connection_encryption import decrypt_config_secrets, decrypt_secret

    conn = op.get_bind()

    for row in conn.execute(sa.select(_dc.c.id, _dc.c.config)).all():
        if row.config is None:
            continue
        conn.execute(
            sa.update(_dc).where(_dc.c.id == row.id).values(config=decrypt_config_secrets(row.config))
        )

    for row in conn.execute(sa.select(_rot.c.id, _rot.c.secret_value)).all():
        if not row.secret_value:
            continue
        conn.execute(
            sa.update(_rot).where(_rot.c.id == row.id).values(secret_value=decrypt_secret(row.secret_value))
        )
