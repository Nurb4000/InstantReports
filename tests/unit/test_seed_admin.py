"""Unit tests for the C1 fix in scripts/seed_admin.py.

Verifies that the standalone seed script no longer seeds a well-known weak
default admin password, matching the startup guard in app/main.py.
"""

import base64

from scripts.seed_admin import _WEAK_PASSWORDS, _resolve_admin_password


class TestResolveAdminPassword:
    """Test INITIAL_ADMIN_PASSWORD resolution (C1)."""

    def test_no_password_generates_strong_random(self, monkeypatch):
        """With no INITIAL_ADMIN_PASSWORD a random strong password is produced."""
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
        email, password = _resolve_admin_password()
        assert email == "admin@example.com"
        # token_urlsafe(24) -> ~32 chars, well above the 8-char minimum.
        assert len(password) >= 8
        # Must not be a known weak placeholder.
        assert password not in _WEAK_PASSWORDS

    def test_generated_passwords_are_unique(self, monkeypatch):
        """Two generations without an env password must differ."""
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
        _, p1 = _resolve_admin_password()
        _, p2 = _resolve_admin_password()
        assert p1 != p2

    def test_custom_email_respected(self, monkeypatch):
        """INITIAL_ADMIN_EMAIL overrides the default email."""
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "root@example.com")
        email, _ = _resolve_admin_password()
        assert email == "root@example.com"

    def test_weak_provided_password_rejected(self, monkeypatch):
        """A provided password that is a known placeholder is rejected."""
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "admin")
        try:
            _resolve_admin_password()
        except SystemExit as e:
            assert "weak" in str(e).lower()
        else:
            raise AssertionError("expected SystemExit for weak password")

    def test_short_provided_password_rejected(self, monkeypatch):
        """A provided password shorter than 8 chars is rejected."""
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "short")
        try:
            _resolve_admin_password()
        except SystemExit:
            pass
        else:
            raise AssertionError("expected SystemExit for short password")

    def test_strong_provided_password_accepted(self, monkeypatch):
        """A provided strong password is returned unchanged."""
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "correct-horse-battery")
        email, password = _resolve_admin_password()
        assert email == "admin@example.com"
        assert password == "correct-horse-battery"

    def test_generated_password_decodes(self, monkeypatch):
        """Generated password is valid urlsafe base64 (sanity check)."""
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
        _, password = _resolve_admin_password()
        # token_urlsafe uses urlsafe base64; padding must be valid.
        base64.urlsafe_b64decode(password + "=" * (-len(password) % 4))
