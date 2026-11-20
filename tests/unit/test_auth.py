"""Unit tests for authentication."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    LDAPConfigError,
    authenticate_ldap_user,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import AuthSource


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


class TestPasswordHashingD1Regression:
    """D1: lock in password-hashing behavior across the known passlib/bcrypt
    failure modes so a future dependency upgrade cannot silently reintroduce
    them.

    passlib[bcrypt]==1.7.4 + bcrypt 4.x historically raised encoding errors on
    non-ASCII passwords (bcrypt operates on bytes) and mishandled the 72-byte
    bcrypt truncation boundary. These tests pin the correct behavior.
    """

    def test_unicode_password_roundtrip(self):
        """Non-ASCII passwords must hash and verify (the passlib/bcrypt
        encoding bug raised TypeError/UnicodeError on such input)."""
        hashed = hash_password("pässwörd—✓unicode")
        assert verify_password("pässwörd—✓unicode", hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_password_longer_than_72_bytes_verifies(self):
        """bcrypt truncates at 72 bytes; a longer password must still verify
        consistently (the stored hash truncates the same way on verify)."""
        hashed = hash_password("a" * 100)
        assert verify_password("a" * 100, hashed) is True

    def test_exactly_72_and_73_bytes_both_verify(self):
        """The 72-byte boundary itself must work on both sides."""
        for length in (72, 73):
            pw = "b" * length
            hashed = hash_password(pw)
            assert verify_password(pw, hashed) is True, f"length {length} failed"

    def test_empty_password_roundtrip(self):
        """An empty password must still hash and verify without error."""
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("x", hashed) is False


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


class _FakeEntry:
    """Minimal stand-in for an ldap3 entry supporting the attribute access used."""

    def __init__(self, dn, email_value, name_value, email_attr="mail", name_attr="cn"):
        self.entry_dn = dn
        self.entry_attributes = {email_attr.lower(), name_attr.lower()}
        self._values = {email_attr.lower(): email_value, name_attr.lower(): name_value}

    def __getitem__(self, key):
        class _Value:
            def __init__(self, value):
                self.value = value

        return _Value(self._values[key.lower()])


class _ScalarResult:
    """Mimics a SQLAlchemy result exposing scalar_one_or_none()."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _ldap_settings(**overrides):
    defaults = dict(
        LDAP_URL="ldaps://ldap.example:636",
        LDAP_SEARCH_BASE="dc=fake",
        LDAP_BIND_DN="cn=sys,dc=fake",
        LDAP_BIND_PASSWORD="service-pw",
        LDAP_USER_ATTR="uid",
        LDAP_EMAIL_ATTR="mail",
        LDAP_NAME_ATTR="cn",
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _fake_ldap3():
    """Build a fake ``ldap3`` module with a *real* ``exceptions`` submodule.

    ``authenticate_ldap_user`` does ``from ldap3.exceptions import LDAPException``,
    which fails against a bare ``MagicMock`` (not a package). A real module with
    an ``LDAPException`` base class lets that import resolve while still letting
    tests drive ``Server``/``Connection`` behavior.
    """
    import types

    ldap3 = types.ModuleType("ldap3")
    ldap3.ALL = "ALL"
    ldap3.SUBTREE = "SUBTREE"
    ldap3.Server = MagicMock()
    ldap3.Connection = MagicMock()

    exc_mod = types.ModuleType("ldap3.exceptions")

    class LDAPException(Exception):
        pass

    class LDAPInvalidCredentialsError(LDAPException):
        pass

    exc_mod.LDAPException = LDAPException
    exc_mod.LDAPInvalidCredentialsError = LDAPInvalidCredentialsError
    ldap3.exceptions = exc_mod

    return ldap3, LDAPException


class TestLdapAuthLogging:
    """LDAP errors are now distinguished, not swallowed into a uniform None.

    The old bare `except Exception: return None` made an LDAP server timeout
    indistinguishable from a wrong password -- hiding misconfiguration and
    aiding user enumeration. ``authenticate_ldap_user`` now raises
    ``LDAPConfigError`` for transport/config failures and returns ``None`` only
    for genuine authentication failures (unknown user / wrong password).
    """

    @pytest.mark.asyncio
    async def test_transport_error_raises_ldap_config_error(self):
        """A failure creating/connecting the server is a backend error, not a
        per-user credential failure: it raises LDAPConfigError and is logged at
        error level with a backend-specific message."""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_logger = MagicMock()

        fake_ldap3, _ = _fake_ldap3()
        fake_ldap3.Server.side_effect = RuntimeError("DNS failed")

        with (
            patch("app.auth.logger", mock_logger),
            patch("app.auth.settings", _ldap_settings(LDAP_URL="ldaps://broken:636")),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            with pytest.raises(LDAPConfigError):
                await authenticate_ldap_user(mock_db, "user@example.com", "pass")

        mock_logger.error.assert_called_once()
        message = mock_logger.error.call_args[0][0]
        assert "backend unreachable" in message.lower()
        # Logs the backend URL and the underlying error so admins can diagnose
        # connectivity/misconfiguration (the user is not yet known at this point).
        assert mock_logger.error.call_args[0][1] == "ldaps://broken:636"

    @pytest.mark.asyncio
    async def test_service_account_bind_error_raises_ldap_config_error(self):
        """A failing service-account bind (bad bind DN/password) is a config
        error -> LDAPConfigError, distinct from a wrong user password."""
        mock_db = AsyncMock(spec=AsyncSession)

        fake_ldap3, _ = _fake_ldap3()
        fake_ldap3.Connection.side_effect = RuntimeError("bind denied")

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            with pytest.raises(LDAPConfigError):
                await authenticate_ldap_user(mock_db, "user@example.com", "pass")

    @pytest.mark.asyncio
    async def test_unknown_user_returns_none(self):
        """No matching LDAP entry is a normal authentication failure (None)."""
        mock_db = AsyncMock(spec=AsyncSession)

        fake_ldap3, _ = _fake_ldap3()
        conn = MagicMock()
        conn.entries = []
        fake_ldap3.Server.return_value = MagicMock()
        fake_ldap3.Connection.return_value = conn

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "nobody@example.com", "pass")

        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_password_returns_none(self):
        """A user bind that does not bind (wrong password) returns None."""
        mock_db = AsyncMock(spec=AsyncSession)

        fake_ldap3, _ = _fake_ldap3()
        server = MagicMock()
        search_conn = MagicMock()
        search_conn.entries = [_FakeEntry("cn=alice,dc=fake", "alice@example.com", "Alice")]
        user_conn = MagicMock()
        user_conn.bound = False  # wrong password -> not bound
        fake_ldap3.Server.return_value = server
        # First Connection = service account, second = user bind.
        fake_ldap3.Connection.side_effect = [search_conn, user_conn]

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "wrongpass")

        assert result is None


class TestLdapTlsEnforcement:
    """D2: the LDAP bind channel must be encrypted (ldaps:// or STARTTLS)."""

    def _server_kwargs(self, fake_ldap3):
        return fake_ldap3.Server.call_args.kwargs

    @pytest.mark.asyncio
    async def test_ldap_url_uses_starttls_by_default(self):
        """A cleartext ldap:// URL negotiates STARTTLS (use_tls=True) by default."""
        mock_db = AsyncMock(spec=AsyncSession)
        fake_ldap3, _ = _fake_ldap3()
        # Empty search result returns None right after the Server is created, so
        # we can assert on the Server kwargs without driving the full bind flow.
        search_conn = MagicMock()
        search_conn.entries = []
        fake_ldap3.Server.return_value = MagicMock()
        fake_ldap3.Connection.return_value = search_conn

        with (
            patch("app.auth.logger", MagicMock()),
            patch(
                "app.auth.settings",
                _ldap_settings(LDAP_URL="ldap://ldap.example:389", LDAP_USE_TLS=True),
            ),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "pass")

        assert result is None
        assert self._server_kwargs(fake_ldap3).get("use_tls") is True

    @pytest.mark.asyncio
    async def test_ldaps_url_does_not_starttls(self):
        """ldaps:// uses implicit TLS; use_tls is not forced on."""
        mock_db = AsyncMock(spec=AsyncSession)
        fake_ldap3, _ = _fake_ldap3()
        search_conn = MagicMock()
        search_conn.entries = []
        fake_ldap3.Server.return_value = MagicMock()
        fake_ldap3.Connection.return_value = search_conn

        with (
            patch("app.auth.logger", MagicMock()),
            patch(
                "app.auth.settings",
                _ldap_settings(LDAP_URL="ldaps://ldap.example:636", LDAP_USE_TLS=True),
            ),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "pass")

        assert result is None
        assert self._server_kwargs(fake_ldap3).get("use_tls") is False

    @pytest.mark.asyncio
    async def test_cleartext_warns_when_tls_disabled(self):
        """ldap:// with LDAP_USE_TLS=false stays cleartext and logs a warning."""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_logger = MagicMock()
        fake_ldap3, _ = _fake_ldap3()
        search_conn = MagicMock()
        search_conn.entries = []
        fake_ldap3.Server.return_value = MagicMock()
        fake_ldap3.Connection.return_value = search_conn

        with (
            patch("app.auth.logger", mock_logger),
            patch(
                "app.auth.settings",
                _ldap_settings(LDAP_URL="ldap://ldap.example:389", LDAP_USE_TLS=False),
            ),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "pass")

        assert result is None
        assert self._server_kwargs(fake_ldap3).get("use_tls") is False
        warnings = [c for c in mock_logger.warning.call_args_list if "cleartext" in c.args[0]]
        assert warnings, "expected a cleartext warning when TLS is disabled on ldap://"


class TestLdapProvisioning:
    """LDAP login provisions a local user row; the write path must be safe.

    Provisioning is a write side-effect on the auth path. It must (a) reuse an
    existing LDAP-backed row instead of duplicating it, and (b) handle a
    concurrent provision that races to the unique ``email`` column by returning
    the existing row rather than swallowing the IntegrityError into a misleading
    "incorrect email or password" for a valid login.
    """

    def _patched_ldap(self, fake_ldap3, search_entries, user_bound=True):
        server = MagicMock()
        search_conn = MagicMock()
        search_conn.entries = search_entries
        user_conn = MagicMock()
        user_conn.bound = user_bound
        fake_ldap3.Server.return_value = server
        fake_ldap3.Connection.side_effect = [search_conn, user_conn]

    @pytest.mark.asyncio
    async def test_existing_ldap_user_is_reused(self):
        """An existing LDAP-backed user is returned without a new insert."""
        existing = MagicMock()
        existing.auth_source = AuthSource.LDAP
        mock_db = AsyncMock(spec=AsyncSession)
        # With a spec'd AsyncMock, execute.return_value is itself awaitable, so
        # point it straight at a sync result object exposing scalar_one_or_none.
        mock_db.execute.return_value = _ScalarResult(existing)

        fake_ldap3, _ = _fake_ldap3()
        self._patched_ldap(fake_ldap3, [_FakeEntry("cn=alice,dc=fake", "alice@example.com", "Alice")])

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "rightpass")

        assert result is existing
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_ldap_user_is_provisioned(self):
        """A first-time LDAP login provisions a new local user row."""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = _ScalarResult(None)

        fake_ldap3, _ = _fake_ldap3()
        self._patched_ldap(fake_ldap3, [_FakeEntry("cn=alice,dc=fake", "alice@example.com", "Alice")])

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "rightpass")

        assert result is not None
        mock_db.commit.assert_called_once()
        provisioned = [call.args[0] for call in mock_db.add.call_args_list]
        assert any(u.auth_source == AuthSource.LDAP for u in provisioned)

    @pytest.mark.asyncio
    async def test_concurrent_provision_unique_violation_returns_existing(self):
        """If a concurrent login already provisioned the same email (unique
        constraint), the IntegrityError is handled by returning the existing row
        instead of failing the valid login."""
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock(spec=AsyncSession)
        # First execute -> no existing user; after the failed insert we re-query
        # and find the now-existing row.
        query_values = iter([None, MagicMock(auth_source=AuthSource.LDAP)])

        async def fake_execute(stmt):
            return _ScalarResult(next(query_values))

        mock_db.execute = AsyncMock(side_effect=fake_execute)

        async def failing_commit():
            raise IntegrityError("duplicate key value", {}, None)

        mock_db.commit = AsyncMock(side_effect=failing_commit)

        fake_ldap3, _ = _fake_ldap3()
        self._patched_ldap(fake_ldap3, [_FakeEntry("cn=alice,dc=fake", "alice@example.com", "Alice")])

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "rightpass")

        assert result is not None
        assert result.auth_source == AuthSource.LDAP
        mock_db.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_email_owned_by_local_user_is_not_overwritten(self):
        """If the email is already a LOCAL user, LDAP login returns None rather
        than clobbering the local row with an LDAP profile."""
        local_user = MagicMock()
        local_user.auth_source = AuthSource.LOCAL
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = _ScalarResult(local_user)

        fake_ldap3, _ = _fake_ldap3()
        self._patched_ldap(fake_ldap3, [_FakeEntry("cn=alice,dc=fake", "alice@example.com", "Alice")])

        with (
            patch("app.auth.logger", MagicMock()),
            patch("app.auth.settings", _ldap_settings()),
            patch.dict(sys.modules, {"ldap3": fake_ldap3, "ldap3.exceptions": fake_ldap3.exceptions}),
        ):
            result = await authenticate_ldap_user(mock_db, "alice@example.com", "rightpass")

        assert result is None
        mock_db.add.assert_not_called()

