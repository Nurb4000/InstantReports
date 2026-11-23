"""Tests for optional TOTP MFA (Gap #10).

Covers the stdlib TOTP service, backup-code hashing/single-use semantics, the
two-step login pending-session state machine, and the opt-in enforcement of the
login flow. The key property under test throughout: MFA is *optional* — accounts
with MFA off log in with a password alone, and nothing enables it by default.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.config import settings
from app.models.pending_session import PendingSession
from app.models.user import AuthSource, User, UserRole
from app.models.user_backup_code import UserBackupCode
from app.services import mfa, pending_session, totp


def _local_user(db: AsyncSession, **kwargs) -> User:
    user = User(
        id=uuid.uuid4(),
        email=kwargs.pop("email", "mfa@example.com"),
        name="MFA User",
        password_hash=hash_password("password123"),
        role=kwargs.pop("role", UserRole.VIEWER),
        auth_source=AuthSource.LOCAL,
        is_active=True,
        **kwargs,
    )
    db.add(user)
    return user


# ---------------------------------------------------------------------------
# TOTP service
# ---------------------------------------------------------------------------


class TestTOTP:
    def test_generate_secret_is_base32_128_bits(self):
        secret = totp.generate_secret()
        # 16 bytes -> 128 bits of entropy; base32 (Authy/Google Authenticator
        # format) decodes back to exactly those 16 bytes. Padding '=' is allowed.
        assert len(totp._decode_secret(secret)) == 16
        assert secret.rstrip("=").isalnum()

    def test_generate_code_verifies_at_same_time(self):
        secret = totp.generate_secret()
        now = 1_700_000_000.0
        code = totp.generate_code(secret, now=now)
        assert len(code) == totp.DIGITS
        assert totp.verify_code(secret, code, now=now) is True

    def test_verify_rejects_wrong_code(self):
        secret = totp.generate_secret()
        now = 1_700_000_000.0
        assert totp.verify_code(secret, "000000", now=now) is False

    def test_verify_tolerates_clock_skew(self):
        secret = totp.generate_secret()
        now = 1_700_000_000.0
        code = totp.generate_code(secret, now=now)
        # A code generated at ``now`` must still verify a period earlier/later.
        assert totp.verify_code(secret, code, now=now - totp.PERIOD_SECONDS) is True
        assert totp.verify_code(secret, code, now=now + totp.PERIOD_SECONDS) is True

    def test_verify_rejects_outside_window(self):
        secret = totp.generate_secret()
        now = 1_700_000_000.0
        code = totp.generate_code(secret, now=now)
        # Two periods outside the default window (1) must fail.
        assert totp.verify_code(secret, code, now=now - 2 * totp.PERIOD_SECONDS) is False

    def test_verify_empty_code_is_false_not_error(self):
        secret = totp.generate_secret()
        assert totp.verify_code(secret, "") is False
        assert totp.verify_code(secret, "   ") is False

    def test_provisioning_uri_format(self):
        import urllib.parse

        secret = totp.generate_secret()
        uri = totp.otpauth_uri("user@example.com", secret, issuer="InstantReports")
        assert uri.startswith("otpauth://totp/")
        params = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
        # The secret's ``=`` padding is URL-encoded in the URI; parse it back.
        assert params["secret"][0] == secret
        assert params["issuer"][0] == "InstantReports"
        # The account label is URL-encoded (":" and "@" are percent-encoded).
        assert urllib.parse.unquote(uri.split("?")[0]).endswith("user@example.com")


# ---------------------------------------------------------------------------
# Backup codes
# ---------------------------------------------------------------------------


class TestBackupCodes:
    @pytest.mark.asyncio
    async def test_prepare_enable_generates_secret_and_codes(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, codes = await mfa.prepare_enable(db_session, user)

        assert secret  # a base32 secret was generated
        assert len(codes) == settings.MFA_BACKUP_CODES_COUNT
        # Secret persisted, but MFA still disabled until confirm.
        assert user.mfa_secret == secret
        assert user.mfa_enabled is False

        rows = (
            await db_session.execute(select(UserBackupCode).where(UserBackupCode.user_id == user.id))
        ).scalars().all()
        assert len(rows) == settings.MFA_BACKUP_CODES_COUNT

    @pytest.mark.asyncio
    async def test_confirm_enable_with_valid_code(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, _ = await mfa.prepare_enable(db_session, user)
        code = totp.generate_code(secret)
        ok = await mfa.confirm_enable(db_session, user, code)

        assert ok is True
        assert user.mfa_enabled is True

    @pytest.mark.asyncio
    async def test_confirm_enable_rejects_bad_code(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, _ = await mfa.prepare_enable(db_session, user)
        ok = await mfa.confirm_enable(db_session, user, "000000", now=1_700_000_000.0)

        assert ok is False
        assert user.mfa_enabled is False

    @pytest.mark.asyncio
    async def test_backup_code_is_single_use(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, codes = await mfa.prepare_enable(db_session, user)
        # Enable MFA so verify_login_code will consult backup codes.
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))

        code = codes[0]
        ok, used_backup = await mfa.verify_login_code(db_session, user, code)
        assert ok is True
        assert used_backup is True

        # Reusing the same backup code now fails.
        ok2, _ = await mfa.verify_login_code(db_session, user, code)
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_totp_code_tried_before_backup(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, codes = await mfa.prepare_enable(db_session, user)
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))

        # A valid TOTP code succeeds and does NOT consume a backup code.
        now = 1_700_000_000.0
        code = totp.generate_code(secret, now=now)
        ok, used_backup = await mfa.verify_login_code(db_session, user, code, now=now)
        assert ok is True
        assert used_backup is False

        remaining = (
            await db_session.execute(select(UserBackupCode).where(UserBackupCode.user_id == user.id))
        ).scalars().all()
        assert len(remaining) == settings.MFA_BACKUP_CODES_COUNT

    @pytest.mark.asyncio
    async def test_disable_mfa_requires_password(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, codes = await mfa.prepare_enable(db_session, user)
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))
        assert user.mfa_enabled is True

        # Wrong password -> not disabled.
        ok = await mfa.disable_mfa(db_session, user, "wrongpassword")
        assert ok is False
        assert user.mfa_enabled is True

        # Correct password -> secret cleared, codes revoked, flag off.
        ok = await mfa.disable_mfa(db_session, user, "password123")
        assert ok is True
        assert user.mfa_enabled is False
        assert user.mfa_secret is None
        remaining = (
            await db_session.execute(select(UserBackupCode).where(UserBackupCode.user_id == user.id))
        ).scalars().all()
        assert list(remaining) == []

    @pytest.mark.asyncio
    async def test_rotate_backup_codes_revokes_old(self, db_session: AsyncSession):
        user = _local_user(db_session)
        db_session.add(user)
        await db_session.commit()

        secret, codes = await mfa.prepare_enable(db_session, user)
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))

        # Consume one old code.
        await mfa.verify_login_code(db_session, user, codes[0])

        new_codes = await mfa.rotate_backup_codes(db_session, user)
        assert len(new_codes) == settings.MFA_BACKUP_CODES_COUNT
        # Old codes are gone; new set is distinct and usable.
        assert codes[0] not in new_codes
        ok, used_backup = await mfa.verify_login_code(db_session, user, new_codes[0])
        assert ok is True
        assert used_backup is True


# ---------------------------------------------------------------------------
# Two-step login pending session
# ---------------------------------------------------------------------------


class TestPendingSession:
    @pytest.mark.asyncio
    async def test_issue_and_consume(self, db_session: AsyncSession):
        token = await pending_session.issue_pending_session(db_session, "user@example.com")
        assert isinstance(token, str) and token

        # Email resolvable while valid.
        email = await pending_session.get_pending_user_email(db_session, token)
        assert email == "user@example.com"

        # Consuming returns the bound email and removes the row (no replay).
        assert await pending_session.consume_pending_session(db_session, token) == "user@example.com"
        assert await pending_session.consume_pending_session(db_session, token) is None

    @pytest.mark.asyncio
    async def test_expired_pending_session_is_invalid(self, db_session: AsyncSession):
        token = await pending_session.issue_pending_session(db_session, "user@example.com")
        # Backdate the expiry so it is already stale.
        row = (
            await db_session.execute(
                select(PendingSession).where(PendingSession.token == token)
            )
        ).scalar_one()
        from datetime import datetime, timedelta, timezone

        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.commit()

        assert await pending_session.get_pending_user_email(db_session, token) is None
        assert await pending_session.consume_pending_session(db_session, token) is None

    @pytest.mark.asyncio
    async def test_unknown_token_is_invalid(self, db_session: AsyncSession):
        assert await pending_session.get_pending_user_email(db_session, "nope") is None
        assert await pending_session.consume_pending_session(db_session, "nope") is None


# ---------------------------------------------------------------------------
# Login flow opt-in behavior (integration)
# ---------------------------------------------------------------------------


class TestTwoStepLogin:
    @pytest.mark.asyncio
    async def test_login_flow_two_step(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Full two-step login: password -> pending cookie -> valid code -> session."""
        # Cookies are marked Secure; tests run over http://test, so allow the
        # pending/session cookies to round-trip through the client jar.
        monkeypatch.setattr(settings, "COOKIE_SECURE", False)

        user = _local_user(db_session, email="twostep@example.com")
        secret, _ = await mfa.prepare_enable(db_session, user)
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))
        await db_session.commit()

        # Step 1: correct password -> 302 to /auth/mfa-verify with pending cookie.
        resp = await client.post(
            "/auth/login",
            data={"email": "twostep@example.com", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/auth/mfa-verify")
        pending = resp.cookies.get("mfa_pending_token")
        assert pending

        # Without completing step 2, the session cookies are NOT set.
        assert not resp.cookies.get("access_token")

        # Step 2: valid code -> full session + redirect.
        code = totp.generate_code(secret)
        resp2 = await client.post(
            "/auth/mfa-verify",
            data={"code": code},
            follow_redirects=False,
        )
        assert resp2.status_code == 302
        assert resp2.headers["location"].endswith("/portal")  # viewer -> portal
        assert resp2.cookies.get("access_token")
        assert resp2.cookies.get("current_user_id")

    @pytest.mark.asyncio
    async def test_mfa_verify_rejects_bad_code(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "COOKIE_SECURE", False)
        user = _local_user(db_session, email="badcode@example.com")
        secret, _ = await mfa.prepare_enable(db_session, user)
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))
        await db_session.commit()

        resp = await client.post(
            "/auth/login",
            data={"email": "badcode@example.com", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Wrong code -> 401 and no session cookie.
        resp2 = await client.post(
            "/auth/mfa-verify",
            data={"code": "000000"},
            follow_redirects=False,
        )
        assert resp2.status_code == 401
        assert not resp2.cookies.get("access_token")

    @pytest.mark.asyncio
    async def test_non_mfa_user_logs_in_directly(self, client: AsyncClient, db_session: AsyncSession):
        """MFA stays optional: an account with MFA off completes login in one step."""
        _local_user(db_session, email="nomfa@example.com")

        resp = await client.post(
            "/auth/login",
            data={"email": "nomfa@example.com", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/portal")
        assert resp.cookies.get("access_token")  # full session in one step
        assert not resp.cookies.get("mfa_pending_token")

    @pytest.mark.asyncio
    async def test_mfa_required_enforcement(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        """With MFA_REQUIRED on, a local account without MFA is redirected to setup."""
        monkeypatch.setattr(settings, "COOKIE_SECURE", False)
        monkeypatch.setattr(settings, "MFA_REQUIRED", True)
        _local_user(db_session, email="enforce@example.com")

        resp = await client.post(
            "/auth/login",
            data={"email": "enforce@example.com", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/auth/mfa-setup")
        # No full session issued yet.
        assert not resp.cookies.get("access_token")

    @pytest.mark.asyncio
    async def test_mfa_setup_then_login(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        """With MFA_REQUIRED on, a user who completed setup logs in via two-step."""
        monkeypatch.setattr(settings, "COOKIE_SECURE", False)
        monkeypatch.setattr(settings, "MFA_REQUIRED", True)
        user = _local_user(db_session, email="setupfirst@example.com")

        # Simulate the user having gone through /auth/mfa-setup (confirmed).
        secret, _ = await mfa.prepare_enable(db_session, user)
        await mfa.confirm_enable(db_session, user, totp.generate_code(secret))
        await db_session.commit()

        # Step 1: password -> pending cookie (enforcement does not block setup users).
        resp = await client.post(
            "/auth/login",
            data={"email": "setupfirst@example.com", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/auth/mfa-verify")

        # Step 2: valid code -> full session.
        resp2 = await client.post(
            "/auth/mfa-verify",
            data={"code": totp.generate_code(secret)},
            follow_redirects=False,
        )
        assert resp2.status_code == 302
        assert resp2.headers["location"].endswith("/portal")
        assert resp2.cookies.get("access_token")
