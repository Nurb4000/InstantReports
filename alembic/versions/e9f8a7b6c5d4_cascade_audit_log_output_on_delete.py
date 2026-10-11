"""cascade audit log output_id on delete

Revision ID: e9f8a7b6c5d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-10 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e9f8a7b6c5d4"
down_revision: str = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("audit_log_output_id_fkey", "audit_log", type_="foreignkey")
    op.create_foreign_key(
        "audit_log_output_id_fkey",
        "audit_log",
        "report_outputs",
        ["output_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("audit_log_output_id_fkey", "audit_log", type_="foreignkey")
    op.create_foreign_key(
        "audit_log_output_id_fkey",
        "audit_log",
        "report_outputs",
        ["output_id"],
        ["id"],
    )
