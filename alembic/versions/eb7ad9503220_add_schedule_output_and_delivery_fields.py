"""add_schedule_output_and_delivery_fields

Revision ID: eb7ad9503220
Revises: 57a68b59befa
Create Date: 2026-08-27 00:15:00.000000

"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'eb7ad9503220'
down_revision: Union[str, None] = '57a68b59befa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('schedules', sa.Column('output_format', sa.String(20), nullable=True))
    op.add_column('schedules', sa.Column('delivery_config', sa.JSON(), nullable=True))
    op.add_column('schedules', sa.Column('recipient_emails', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('schedules', 'recipient_emails')
    op.drop_column('schedules', 'delivery_config')
    op.drop_column('schedules', 'output_format')
