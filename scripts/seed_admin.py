"""Seed script to create initial admin user."""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime

# Add app to path
sys.path.insert(0, '/app')

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.models.user import User, AuthSource, UserRole


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
            # Create default admin user
            admin = User(
                id=uuid.uuid4(),
                email="admin@example.com",
                name="Admin User",
                password_hash=hash_password("admin"),
                role=UserRole.ADMIN,
                auth_source=AuthSource.LOCAL,
                is_active=True,
                created_at=datetime.utcnow(),
            )
            db.add(admin)
            await db.commit()
            print(f"Created default admin user: admin@example.com / admin")
        else:
            print(f"Found {len(users)} existing user(s), skipping admin creation")

    await engine.dispose()


if __name__ == "__main__":
    import os
    database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://ir:secret@postgres:5432/instantreports")
    asyncio.run(seed_admin_user(database_url))
