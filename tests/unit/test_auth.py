"""Unit tests for authentication."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    authenticate_ldap_user,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


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


class TestSecretKeyValidation:
    """The JWT signing secret must never be the well-known development
    placeholder — with it, anyone can forge an admin token (full auth bypass)."""

    def test_placeholder_secret_key_is_rejected(self, monkeypatch):
        from app.config import Settings

        monkeypatch.setenv("SECRET_KEY", "change-me-in-production")
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings()

    def test_real_secret_key_is_accepted(self, monkeypatch):
        from app.config import Settings

        monkeypatch.setenv("SECRET_KEY", "a-strong-test-secret")
        assert Settings().SECRET_KEY == "a-strong-test-secret"


class TestUserModel:
    """Test User model."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session: AsyncSession):
        """Should create a user in the database."""
        import uuid

        from app.models.user import AuthSource, User, UserRole

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
        import uuid

        from app.models.user import AuthSource, User, UserRole

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


class TestLdapAuthLogging:
    """LDAP auth failures must be logged, not swallowed silently.

    Regression: the bare `except Exception: return None` in
    ``authenticate_ldap_user`` made an LDAP server timeout indistinguishable
    from a wrong password — admins could not diagnose connectivity issues
    from the login UI.
    """

    @pytest.mark.asyncio
    async def test_ldap_exception_is_logged(self):
        """An exception inside authenticate_ldap_user must call logger.error."""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_logger = MagicMock()

        fake_ldap3 = MagicMock()
        fake_ldap3.Server.side_effect = RuntimeError("DNS failed")

        with (
            patch("app.auth.logger", mock_logger),
            patch("app.auth.settings", MagicMock(LDAP_URL="ldaps://broken:636", LDAP_SEARCH_BASE="dc=fake")),
            patch.dict(sys.modules, {"ldap3": fake_ldap3}),
        ):
            result = await authenticate_ldap_user(mock_db, "user@example.com", "pass")

        assert result is None
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert call_args[0][0] == "LDAP authentication failed for %s: %s"
        assert call_args[0][1] == "user@example.com"
