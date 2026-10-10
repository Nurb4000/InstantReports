"""merge query-history/report-templates and api-key-hashing heads

The repository had two head revisions (b2c3d4e5f6a7 and c7d8e9f0a1b2) branching
from a1b2c3d4e5f6, so ``alembic upgrade head`` failed with "Multiple head
revisions are present". This empty merge revision rejoins them into a single
linear head.

Revision ID: d8e9f0a1b2c3
Revises: b2c3d4e5f6a7, c7d8e9f0a1b2
Create Date: 2026-10-10 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "d8e9f0a1b2c3"
down_revision: tuple[str, str] = ("b2c3d4e5f6a7", "c7d8e9f0a1b2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
