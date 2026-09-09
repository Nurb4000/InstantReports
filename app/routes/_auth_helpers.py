"""Shared authentication/authorization helpers for route modules.

Centralises the "extract role/auth_source value from a User object, handling
both Enum members and plain strings" pattern so every route agrees on the
behaviour. Modules that need these functions import from this package rather
than copying the helper inline.
"""
from __future__ import annotations


def get_role_value(user) -> str | None:
    """Return the user's role as a plain string.

    Handles both ``UserRole`` enum members (``user.role.value``) and raw
    strings stored in older rows, so callers do not need to branch.
    """
    if user is None:
        return None
    role = getattr(user, "role", None)
    if role is None:
        return None
    return role.value if hasattr(role, "value") else str(role)


def get_auth_source_value(user) -> str | None:
    """Return the user's auth source as a plain string.

    Handles both ``AuthSource`` enum members and raw strings.
    """
    if user is None:
        return None
    source = getattr(user, "auth_source", None)
    if source is None:
        return None
    return source.value if hasattr(source, "value") else str(source)


def check_role(user, *allowed: str) -> bool:
    """Return True if ``user`` has one of the allowed role strings.

    Returns False for None users so callers can chain::

        if not check_role(current_user, "admin", "designer"):
            raise HTTPException(403)
    """
    if not user:
        return False
    role = get_role_value(user)
    if role is None:
        return False
    return role in allowed
