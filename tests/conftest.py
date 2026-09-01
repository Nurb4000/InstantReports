"""Test configuration and fixtures."""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.user import User, UserRole, AuthSource
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.ext.compiler import compiles


# The test DB is SQLite, but the models declare Postgres-only types. Scope each
# override to the sqlite dialect so the schema can be created locally; Postgres
# compilation is unaffected.
@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@compiles(BYTEA, "sqlite")
def _bytea_sqlite(element, compiler, **kw):
    return "BLOB"


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):
    return "VARCHAR(36)"


# Test database URL (uses SQLite for tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

# Create test engine
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a new database session for each test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_factory() as session:
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
        password_hash=__import__("app.auth").hash_password("testpass"),
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
    assert response.status_code == 307

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
        password_hash=__import__("app.auth").hash_password("password123"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user
