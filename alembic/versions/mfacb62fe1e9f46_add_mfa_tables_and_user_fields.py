"""add_mfa_tables_and_user_fields

Optional TOTP MFA for local accounts (Gap #10). Adds the ``mfa_secret`` /
``mfa_enabled`` columns to ``users`` plus two server-side tables:
``pending_sessions`` (the short-lived second login step) and
``user_backup_codes`` (single-use recovery codes, stored hashed).

MFA is opt-in: ``mfa_enabled`` defaults to False and nothing enables it by
default. Enforcement of an org-wide requirement is a separate config toggle
(settings.MFA_REQUIRED), not this schema.

Revision ID: mfacb62fe1e9f46
Revises: b1c2d3e4f5a6
Create Date: 2026-11-22 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mfacb62fe1e9f46"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New user MFA columns. mfa_enabled is NOT NULL but existing rows default to
    # off (MFA stays optional), so give them a server-side default.
    op.add_column(
        "users",
        sa.Column("mfa_secret", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "mfa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Second login step: a short-lived, user-bound, expiring record keyed by the
    # pending-session cookie token (mirrors revoked_tokens).
    op.create_table(
        "pending_sessions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_pending_sessions_token", "pending_sessions", ["token"], unique=True)
    op.create_index("idx_pending_sessions_user_email", "pending_sessions", ["user_email"])

    # Single-use recovery codes, stored only as salted SHA-256 hashes.
    op.create_table(
        "user_backup_codes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_user_backup_codes_user_id", "user_backup_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_user_backup_codes_user_id", table_name="user_backup_codes")
    op.drop_table("user_backup_codes")
    op.drop_index("idx_pending_sessions_user_email", table_name="pending_sessions")
    op.drop_index("idx_pending_sessions_token", table_name="pending_sessions")
    op.drop_table("pending_sessions")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")
