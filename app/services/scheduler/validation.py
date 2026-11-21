"""Validation for scheduler input (Gap #8).

Recurring schedules are stored as raw strings (``cron_expression``, ``timezone``)
and only turned into an APScheduler trigger at sync time
(``CronTrigger.from_crontab(cron, timezone=tz)``). If either value is invalid the
trigger build fails deep in the scheduler loop, so a bad value written via the
create/update routes could silently break schedule execution.

These helpers validate *before* persistence by reusing the exact same resolvers
the scheduler uses at runtime -- apscheduler's ``CronTrigger.from_crontab`` for
cron and pytz (which APScheduler uses internally) for timezones -- so validation
never diverges from what actually runs. Both raise ``ValueError`` with a clear,
user-facing message; callers map that to HTTP 400.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def validate_cron_expression(cron_expression: str | None) -> None:
    """Validate a cron expression using APScheduler's own resolver.

    ``None``/empty/whitespace-only values are treated as valid (the field is
    optional; one-shot schedules omit it). Anything else must parse as a 5-field
    cron expression via :meth:`CronTrigger.from_crontab`, the same call the
    scheduler makes, so an invalid expression fails here instead of at sync time.
    """
    if not cron_expression or not str(cron_expression).strip():
        return

    from apscheduler.triggers.cron import CronTrigger

    # Reuse the runtime resolver so accepted/rejected sets match exactly.
    CronTrigger.from_crontab(str(cron_expression))


def validate_timezone(timezone: str | None) -> None:
    """Validate an IANA timezone name using pytz (APScheduler's internal resolver).

    ``None``/empty values are invalid: a schedule needs a concrete timezone, and
    an empty/unknown name raises ``UnknownTimeZoneError`` when the trigger is
    built. Raises ``ValueError`` so callers catch a single exception type.
    """
    if timezone is None or not str(timezone).strip():
        raise ValueError("Timezone is required")

    import pytz
    from pytz.exceptions import UnknownTimeZoneError

    try:
        pytz.timezone(str(timezone))
    except UnknownTimeZoneError as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc
