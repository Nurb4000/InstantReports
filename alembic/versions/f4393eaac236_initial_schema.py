"""initial_schema

Revision ID: f4393eaac236
Revises: 
Create Date: 2026-08-26 16:12:55.947294

"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4393eaac236'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="viewer"),
        sa.Column("auth_source", sa.String(50), nullable=False, server_default="local"),
        sa.Column("ldap_dn", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        "data_connections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connector_type", sa.String(50), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        "schedules",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("report_id", sa.UUID(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "deliveries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("schedule_id", sa.UUID(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delivery_type", sa.String(50), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "delivery_recipients",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("delivery_id", sa.UUID(), sa.ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("include_in_portal", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.create_table(
        "report_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("report_id", sa.UUID(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("commit_message", sa.Text(), nullable=False),
        sa.Column("diff_summary", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", "version_number"),
    )

    op.create_table(
        "report_tags",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("report_id", sa.UUID(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("tag_name", sa.String(100), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", "tag_name"),
    )

    op.create_table(
        "report_comments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("report_id", sa.UUID(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "report_outputs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("report_id", sa.UUID(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("schedule_id", sa.UUID(), sa.ForeignKey("schedules.id"), nullable=True),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("parameters_used", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("generated_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("report_id", sa.UUID(), sa.ForeignKey("reports.id"), nullable=True),
        sa.Column("schedule_id", sa.UUID(), sa.ForeignKey("schedules.id"), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("output_id", sa.UUID(), sa.ForeignKey("report_outputs.id"), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("idx_report_versions_lookup", "report_versions", ["report_id", sa.text("version_number DESC")])
    op.create_index("idx_report_comments_lookup", "report_comments", ["report_id", sa.text("version_number DESC")])


def downgrade() -> None:
    op.drop_index("idx_report_comments_lookup", table_name="report_comments")
    op.drop_index("idx_report_versions_lookup", table_name="report_versions")
    op.drop_table("audit_log")
    op.drop_table("report_outputs")
    op.drop_table("report_comments")
    op.drop_table("report_tags")
    op.drop_table("report_versions")
    op.drop_table("delivery_recipients")
    op.drop_table("deliveries")
    op.drop_table("schedules")
    op.drop_table("data_connections")
    op.drop_table("reports")
    op.drop_table("users")
