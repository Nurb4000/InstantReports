"""Unit tests for authentication."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, verify_password, create_access_token, decode_access_token
from app.models.user import User, UserRole, AuthSource


class TestPasswordHashing:
    """Test password hashing and verification."""

    def test_hash_password_returns_string(self):
        """Hashed password should be a string."""
        hashed = hash_password("testpassword")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_verify_password_correct(self):
        """Should verify correct password."""
        hashed = hash_password("testpassword")
        assert verify_password("testpassword", hashed) is True

    def test_verify_password_incorrect(self):
        """Should reject incorrect password."""
        hashed = hash_password("testpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_different_hashes_for_same_password(self):
        """Each hash should be unique (salted)."""
        hash1 = hash_password("testpassword")
        hash2 = hash_password("testpassword")
        assert hash1 != hash2


class TestJWTTokens:
    """Test JWT token creation and decoding."""

    def test_create_access_token(self):
        """Should create a valid JWT token."""
        token = create_access_token(data={"sub": "test@example.com"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_access_token(self):
        """Should decode a valid token."""
        token = create_access_token(data={"sub": "test@example.com"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "test@example.com"

    def test_decode_invalid_token(self):
        """Should return None for invalid token."""
        payload = decode_access_token("invalid-token")
        assert payload is None


class TestUserModel:
    """Test User model."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session: AsyncSession):
        """Should create a user in the database."""
        from app.models.user import User, UserRole, AuthSource
        import uuid

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
        await db_session.refresh(user)

        assert user.id is not None
        assert user.email == "test@example.com"
        assert user.role == UserRole.ADMIN

    @pytest.mark.asyncio
    async def test_user_roles(self, db_session: AsyncSession):
        """Should support different user roles."""
        from app.models.user import User, UserRole, AuthSource
        import uuid

        for role in [UserRole.ADMIN, UserRole.DESIGNER, UserRole.VIEWER]:
            user = User(
                id=uuid.uuid4(),
                email=f"{role.value}@example.com",
                name=f"Test {role.value}",
                password_hash=hash_password("testpass"),
                role=role,
                auth_source=AuthSource.LOCAL,
                is_active=True,
            )
            db_session.add(user)

        await db_session.commit()

        # Verify all roles were created
        from sqlalchemy import select
        result = await db_session.execute(select(User))
        users = result.scalars().all()
        assert len(users) == 3
