"""Seed script to create initial admin user.

C1 fix: refuse to seed a well-known weak default (matching the startup guard in
app/main.py). If INITIAL_ADMIN_PASSWORD is not provided, a random strong password
is generated and printed so the operator can capture it. A provided password that
is too short or a known placeholder is rejected.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
import uuid
from datetime import datetime

# Add app to path
sys.path.insert(0, '/app')

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.models.user import User, AuthSource, UserRole

_WEAK_PASSWORDS = {"admin", "password", "12345678", "changeme", "secret"}


def _resolve_admin_password() -> tuple[str, str]:
    """Return (email, password) for the admin user.

    Reads INITIAL_ADMIN_EMAIL / INITIAL_ADMIN_PASSWORD from the environment. When
    no password is supplied a random strong one is generated and returned so the
    caller can display it. A supplied password that is weak is rejected.
    """
    email = os.getenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
    provided = os.getenv("INITIAL_ADMIN_PASSWORD")

    if not provided:
        # Generate a strong random password and let the operator capture it.
        return email, secrets.token_urlsafe(24)

    if len(provided) < 8 or provided in _WEAK_PASSWORDS:
        raise SystemExit(
            "Refusing to seed a weak admin password.\n"
            "Set INITIAL_ADMIN_PASSWORD to a strong value (>= 8 chars, not a "
            f"common placeholder like {'/'.join(sorted(_WEAK_PASSWORDS))}).\n"
            "Leave it unset to have a random strong password generated for you."
        )

    return email, provided


async def seed_admin_user(database_url: str):
    """Create initial admin user if none exists."""
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Check if any users exist
        from sqlalchemy import select
        result = await db.execute(select(User))
        users = result.scalars().all()

        if not users:
            email, password = _resolve_admin_password()
            # Create default admin user
            admin = User(
                id=uuid.uuid4(),
                email=email,
                name="Admin User",
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
                auth_source=AuthSource.LOCAL,
                is_active=True,
                created_at=datetime.utcnow(),
            )
            db.add(admin)
            await db.commit()
            print("=" * 56)
            print("Initial admin user created")
            print(f"  email:    {email}")
            print(f"  password: {password}")
            print("=" * 56)
            print("Save the password above; it will not be shown again.")
        else:
            print(f"Found {len(users)} existing user(s), skipping admin creation")

    await engine.dispose()


if __name__ == "__main__":
    database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://ir:secret@postgres:5432/instantreports")
    asyncio.run(seed_admin_user(database_url))
