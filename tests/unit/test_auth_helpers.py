"""Tests for shared auth helpers in app.routes._auth_helpers."""
from __future__ import annotations

from app.routes._auth_helpers import check_role, get_auth_source_value, get_role_value


class _FakeUser:
    """Minimal stand-in for the User model — enough shape to exercise the helpers."""

    def __init__(self, role, auth_source):
        self.role = role
        self.auth_source = auth_source


class _FakeEnumRole:
    """Mimics a SQLAlchemy Enum member (has .value)."""

    def __init__(self, val):
        self.value = val


class _FakeEnumSource:
    """Mimics an AuthSource enum member."""

    def __init__(self, val):
        self.value = val


class TestGetRoleValue:
    def test_returns_none_for_missing_user(self):
        assert get_role_value(None) is None

    def test_returns_none_when_no_role_attr(self):
        user = _FakeUser(None, None)
        user.role = None
        assert get_role_value(user) is None

    def test_handles_plain_string_role(self):
        user = _FakeUser("admin", "local")
        assert get_role_value(user) == "admin"

    def test_handles_enum_role(self):
        user = _FakeUser(_FakeEnumRole("designer"), "local")
        assert get_role_value(user) == "designer"

    def test_handles_role_as_int(self):
        user = _FakeUser(3, "local")
        assert get_role_value(user) == "3"


class TestGetAuthSourceValue:
    def test_returns_none_for_missing_user(self):
        assert get_auth_source_value(None) is None

    def test_handles_plain_string_source(self):
        user = _FakeUser("admin", "ldap")
        assert get_auth_source_value(user) == "ldap"

    def test_handles_enum_source(self):
        user = _FakeUser("admin", _FakeEnumSource("local"))
        assert get_auth_source_value(user) == "local"


class TestCheckRole:
    def test_returns_false_for_none_user(self):
        assert check_role(None, "admin") is False

    def test_returns_true_when_role_matches(self):
        user = _FakeUser("admin", "local")
        assert check_role(user, "admin", "designer") is True

    def test_returns_false_when_role_not_allowed(self):
        user = _FakeUser("viewer", "local")
        assert check_role(user, "admin", "designer") is False

    def test_handles_enum_role(self):
        user = _FakeUser(_FakeEnumRole("designer"), "local")
        assert check_role(user, "admin", "designer") is True
