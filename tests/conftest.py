"""Test configuration and fixtures."""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles

# app.config instantiates Settings() at import time; the SECRET_KEY validator
# rejects the development placeholder, so ensure a real key is set before any
# app module is imported. Without this, JWT decode/validate would raise in tests.
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-do-not-use-in-production-0000000000"
)

from app.auth import hash_password
from app.config import settings
from app.database import Base, get_db
from app.models.user import AuthSource, User, UserRole

# CSRF enforcement is tested explicitly in test_csrf.py; keep it off for the rest
# of the suite so existing POST/PUT/DELETE tests are not required to carry a token.
settings.CSRF_ENABLED = False


# The test models declare Postgres-only types. The three ``@compiles`` overrides
# below map those types to their SQLite equivalents so the suite can run against a
# local SQLite file with no database server -- an offline convenience only. On
# PostgreSQL, which is the target and default, these overrides are inert and the
# schema is created exactly as it compiles in production (no type masking).
@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@compiles(BYTEA, "sqlite")
def _bytea_sqlite(element, compiler, **kw):
    return "BLOB"


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):
    return "VARCHAR(36)"


# Test database URL. Defaults to the production PostgreSQL instance so tests run
# against the same dialect and schema as production (parity). Override with the
# TEST_DATABASE_URL env var to run offline against a local SQLite file when no
# server is available, e.g.:
#   TEST_DATABASE_URL="sqlite+aiosqlite:///./test.db" pytest
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://ir:secret@localhost:5432/instantreports",
)

@pytest_asyncio.fixture(scope="function")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Fresh async engine per test.

    A module-level shared engine reuses pooled asyncpg connections across the
    function-scoped event loops pytest-asyncio assigns to each test. asyncpg binds
    a connection to the loop that first used it, so reusing that connection under a
    later (already-closed) loop raises ``InterfaceError: another operation is in
    progress`` -- which is why the suite passed on aiosqlite (lenient) but failed on
    asyncpg (strict). A per-test engine gives every test its own connections, bound
    only to that test's loop, and disposes them afterwards for full isolation.
    """
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="function")
def event_loop() -> Generator:
    """Create a fresh event loop for each async test.

    Function scope (rather than session scope) is required because some tests
    call ``asyncio.run()``, which closes its own loop and leaves the process's
    "current event loop" unset. A session-scoped loop then leaks that bad state
    into later async tests (``RuntimeError: There is no current event loop``).
    A per-test loop is created fresh and registered as current by pytest-asyncio
    before every test, so earlier ``asyncio.run()`` calls cannot contaminate it.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Create a new database session for each test."""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    async def override_get_db():
        yield db_session

    app = __import__("app.main", fromlist=["app"]).app
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def auth_client(client: AsyncClient, db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an authenticated test client."""
    # Create a test user
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        name="Test User",
        password_hash=hash_password("testpass"),
        role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    # Login
    response = await client.post(
        "/auth/login",
        data={"email": "test@example.com", "password": "testpass"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    # Get the token from the cookie
    token = response.cookies.get("access_token")
    client.headers["Cookie"] = f"access_token={token}"

    yield client


@pytest_asyncio.fixture(scope="function")
async def test_user(db_session: AsyncSession) -> User:
    """Create and return a test user."""
    user = User(
        id=uuid.uuid4(),
        email="user@example.com",
        name="Test User",
        password_hash=hash_password("password123"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user
