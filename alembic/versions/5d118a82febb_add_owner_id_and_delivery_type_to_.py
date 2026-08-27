"""add_owner_id_and_delivery_type_to_schedules

Revision ID: 5d118a82febb
Revises: eb7ad9503220
Create Date: 2026-08-27 00:30:00.000000

"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5d118a82febb'
down_revision: Union[str, None] = 'eb7ad9503220'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns without foreign key first
    op.add_column('schedules', sa.Column('delivery_type', sa.String(50), nullable=False, server_default='email'))
    op.add_column('schedules', sa.Column('owner_id', sa.UUID(), nullable=True))
    
    # Update existing records to use created_by as owner_id
    op.execute("""
        UPDATE schedules 
        SET owner_id = created_by 
        WHERE owner_id IS NULL
    """)
    
    # Make owner_id NOT NULL now that all records have a value
    op.alter_column('schedules', 'owner_id', nullable=False)
    
    # Now add the foreign key constraint
    op.create_foreign_key('fk_schedules_owner_id', 'schedules', 'users', ['owner_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_schedules_owner_id', 'schedules', type_='foreignkey')
    op.drop_column('schedules', 'owner_id')
    op.drop_column('schedules', 'delivery_type')
