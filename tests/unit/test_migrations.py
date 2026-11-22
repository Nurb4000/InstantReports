"""Tests for Alembic migration graph integrity and idempotency.

These exercise the Alembic migration logic against PostgreSQL (the app's real
backend) rather than SQLite, so any FK / type constraints that SQLite silently
ignores are enforced here too. The migration itself uses generic SQL, so it is
run against a throwaway ``api_keys`` table created on an isolated PostgreSQL
engine; the schema is dropped before teardown so repeated runs stay isolated.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import TEST_DATABASE_URL

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
    plaintext = "my-plaintext-key"
    already = hashlib.sha256(b"already-hashed").hexdigest()

    engine = create_async_engine(TEST_DATABASE_URL)

    def _migrate(sync_conn):
        # Alembic operations run synchronously; MigrationContext/Operations need
        # a sync connection.
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx) as op:
            module.op = op
            module.upgrade()
            module.upgrade()

    async def _run():
        async with engine.begin() as conn:
            # Throwaway table on the real backend; dropped before teardown so
            # repeated runs stay isolated (conftest's create_all is idempotent).
            await conn.execute(
                text(
                    "CREATE TABLE api_keys ("
                    "id SERIAL PRIMARY KEY, key TEXT)"
                )
            )
            await conn.execute(
                text("INSERT INTO api_keys (key) VALUES (:k)"), {"k": plaintext}
            )
            await conn.execute(
                text("INSERT INTO api_keys (key) VALUES (:k)"), {"k": already}
            )

            # run_sync sets up the greenlet context asyncpg needs for sync IO.
            await conn.run_sync(_migrate)

            result = await conn.execute(text("SELECT id, key FROM api_keys"))
            rows = dict((r[0], r[1]) for r in result.all())

        assert rows[1] == hashlib.sha256(plaintext.encode()).hexdigest()
        assert rows[2] == already

        # Isolation: clear the schema so leftover rows don't leak into later
        # runs (conftest's create_all is idempotent). Done in the same loop as
        # the migration so asyncpg's connection stays bound to one event loop.
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS api_keys CASCADE"))

        await engine.dispose()

    asyncio.run(_run())
