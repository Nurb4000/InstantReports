"""Tests for Alembic migration graph integrity and idempotency."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_hash_migration():
    path = _REPO_ROOT / "alembic" / "versions" / "b2c3d4e5f6a7_hash_existing_api_keys.py"
    spec = importlib.util.spec_from_file_location("_hash_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alembic_has_single_head():
    """Regression (H5): two heads made `alembic upgrade head` fail."""
    script = ScriptDirectory.from_config(Config(str(_REPO_ROOT / "alembic.ini")))
    heads = list(script.get_heads())
    assert len(heads) == 1  # Exactly one head after merging and any additional migrations


def test_hash_api_keys_migration_is_idempotent():
    """Regression (H6): re-running the migration must not double-hash keys."""
    module = _load_hash_migration()
    engine = create_engine("sqlite://")
    plaintext = "my-plaintext-key"
    already = hashlib.sha256(b"already-hashed").hexdigest()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, key TEXT)"))
        conn.execute(
            text("INSERT INTO api_keys (id, key) VALUES (1, :k)"), {"k": plaintext}
        )
        conn.execute(
            text("INSERT INTO api_keys (id, key) VALUES (2, :k)"), {"k": already}
        )

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx) as op:
            module.op = op
            module.upgrade()
            module.upgrade()

        rows = dict(conn.execute(text("SELECT id, key FROM api_keys")).all())

    assert rows[1] == hashlib.sha256(plaintext.encode()).hexdigest()
    assert rows[2] == already
