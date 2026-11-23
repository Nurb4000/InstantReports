"""add_secret_rotation_tables

Supports Gap #11: secrets rotation with a grace period.

Two tables back the two rotation mechanisms:

- ``api_key_rotations``: when an API key is rotated, the retired secret's hash
  is stored here with a ``valid_until`` timestamp. While ``valid_until`` is in
  the future the old secret still validates (grace period); afterwards it is
  pruned and rejected. The current secret stays on ``api_keys.key``.
- ``connection_credential_rotations``: when a connection credential is rotated,
  the retired plaintext value is stored here (hashed for at-rest protection)
  with a ``valid_until``. ``resolve_connection_config`` re-exposes in-grace
  values so scheduled reports/webhooks that still hold the old secret keep
  working until the grace window closes.

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-11-22 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_key_rotations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("api_key_id", sa.UUID(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_api_key_rotations_key_hash",
        "api_key_rotations",
        ["key_hash"],
        unique=False,
    )
    op.create_index(
        "idx_api_key_rotations_api_key_id",
        "api_key_rotations",
        ["api_key_id"],
        unique=False,
    )
    op.execute(
        "ALTER TABLE api_key_rotations "
        "ADD CONSTRAINT fk_api_key_rotations_api_key_id "
        "FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE"
    )

    op.create_table(
        "connection_credential_rotations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("secret_value", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_conn_cred_rotations_lookup",
        "connection_credential_rotations",
        ["connection_id", "field_name"],
        unique=False,
    )
    op.execute(
        "ALTER TABLE connection_credential_rotations "
        "ADD CONSTRAINT fk_conn_cred_rotations_connection_id "
        "FOREIGN KEY (connection_id) REFERENCES data_connections(id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    # Drop named FK constraints and the explicitly-created indexes BEFORE the
    # tables (op.create_index indexes are not removed by drop_table).
    op.execute("ALTER TABLE api_key_rotations DROP CONSTRAINT fk_api_key_rotations_api_key_id")
    op.drop_index("idx_api_key_rotations_api_key_id", table_name="api_key_rotations")
    op.drop_index("idx_api_key_rotations_key_hash", table_name="api_key_rotations")
    op.drop_table("api_key_rotations")

    op.execute("ALTER TABLE connection_credential_rotations DROP CONSTRAINT fk_conn_cred_rotations_connection_id")
    op.drop_index("idx_conn_cred_rotations_lookup", table_name="connection_credential_rotations")
    op.drop_table("connection_credential_rotations")
