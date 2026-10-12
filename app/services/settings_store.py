from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Setting attribute name -> caster that turns the stored string back into the
# right Python type. Only keys listed here may be persisted/loaded; anything
# else in the table is ignored.
_CASTERS: dict[str, Callable[[str], Any]] = {
    "SMTP_HOST": str,
    "SMTP_PORT": int,
    "SMTP_USER": str,
    "SMTP_PASSWORD": str,
    "SMTP_FROM": str,
    "SMTP_USE_TLS": _to_bool,
    "SMTP_SUBJECT_TEMPLATE": str,
    "SMTP_BODY_TEMPLATE": str,
    "LDAP_URL": str,
    "LDAP_BIND_DN": str,
    "LDAP_BIND_PASSWORD": str,
    "LDAP_SEARCH_BASE": str,
    "AI_ENABLED": _to_bool,
    "AI_BASE_URL": str,
    "AI_API_KEY": str,
    "AI_MODEL": str,
}

# Secrets that must never be rendered back into the admin page.
SECRET_SETTINGS: frozenset[str] = frozenset(
    {"SMTP_PASSWORD", "LDAP_BIND_PASSWORD", "AI_API_KEY"}
)


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def load_persisted_settings(db: AsyncSession) -> int:
    """Apply DB-persisted overrides onto the in-memory settings singleton.

    Returns the number of overrides applied.
    """
    result = await db.execute(select(AppSetting))
    applied = 0
    for row in result.scalars():
        caster = _CASTERS.get(row.key)
        if caster is None:
            continue
        try:
            setattr(settings, row.key, caster(row.value))
            applied += 1
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Ignoring persisted setting %s=%r: %s", row.key, row.value, exc
            )
    return applied


async def save_setting_updates(db: AsyncSession, updates: dict[str, Any]) -> None:
    """Upsert the given settings and commit. Unknown keys are ignored."""
    for key, value in updates.items():
        if key not in _CASTERS:
            continue
        serialized = _serialize(value)
        existing = await db.get(AppSetting, key)
        if existing is None:
            db.add(AppSetting(key=key, value=serialized))
        else:
            existing.value = serialized
    await db.commit()
