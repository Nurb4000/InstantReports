"""Unit tests for the query builder database adapters (multi-backend support)."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.services.query_builder.adapter import execute_query, fetch_sqlite_schema


def _seed_sqlite(db_path):
    import aiosqlite

    async def _seed():
        conn = await aiosqlite.connect(db_path)
        await conn.execute(
            "CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        await conn.execute(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category_id INTEGER,
                FOREIGN KEY(category_id) REFERENCES categories(id)
            )
            """
        )
        await conn.executemany(
            "INSERT INTO categories (name) VALUES (?)", [("A",), ("B",)]
        )
        await conn.executemany(
            "INSERT INTO products (name, category_id) VALUES (?, ?)",
            [("P1", 1), ("P2", 1), ("P3", 2)],
        )
        await conn.commit()
        await conn.close()

    return _seed()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def _make_seeded_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    asyncio_run(_seed_sqlite(path))
    return path


@pytest.fixture()
def seeded_db():
    path = _make_seeded_db()
    yield path
    os.remove(path)


async def test_fetch_sqlite_schema_detects_pk_and_fk(seeded_db):
    schema = await fetch_sqlite_schema({"database": seeded_db})
    names = {t.name: t for t in schema.tables}
    assert "categories" in names and "products" in names

    products = names["products"]
    by_name = {c.name: c for c in products.columns}
    assert by_name["id"].is_primary_key is True
    assert by_name["category_id"].is_foreign_key is True
    assert by_name["category_id"].foreign_key_table == "categories"
    assert by_name["category_id"].foreign_key_column == "id"

    categories = names["categories"]
    cat_by_name = {c.name: c for c in categories.columns}
    assert cat_by_name["id"].is_primary_key is True


async def test_execute_sqlite_query_and_limit(seeded_db):
    rows = await execute_query(
        "sqlite", {"database": seeded_db}, "SELECT name FROM products ORDER BY id"
    )
    assert [r["name"] for r in rows] == ["P1", "P2", "P3"]

    limited = await execute_query(
        "sqlite", {"database": seeded_db}, "SELECT * FROM products", limit=2
    )
    assert len(limited) == 2


async def test_execute_unsupported_connector_raises():
    with pytest.raises(ValueError):
        await execute_query("oracle", {}, "SELECT 1")
