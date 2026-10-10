"""hash_existing_api_keys

Store API keys as SHA-256 hashes instead of plaintext. Existing rows created
before this migration hold plaintext keys, so this migrates them in place.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-05 12:00:00.000000

"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A SHA-256 hex digest is exactly 64 lowercase hex characters; anything else is
# still a plaintext key.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _is_hashed(key: str) -> bool:
    return bool(_SHA256_HEX_RE.match(key))


def upgrade() -> None:
    # Idempotent: only plaintext keys are hashed, so re-running the migration
    # (or running it over a partially-migrated table) never double-hashes a key.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, key FROM api_keys")).all()
    for row in rows:
        key = row.key
        if key and not _is_hashed(key):
            conn.execute(
                sa.text("UPDATE api_keys SET key = :k WHERE id = :id"),
                {"k": _hash(key), "id": row.id},
            )


def downgrade() -> None:
    # SHA-256 is one-way; the plaintext keys cannot be recovered. Any key rows
    # are deleted so stale/invalid keys are never mistaken for live ones — they
    # must be regenerated. This DB is disposable in development.
    op.execute(sa.text("DELETE FROM api_keys WHERE key IS NOT NULL"))
