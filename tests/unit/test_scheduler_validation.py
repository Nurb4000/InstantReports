"""Unit tests for scheduler input validation (Gap #8).

Covers :func:`validate_cron_expression` and :func:`validate_timezone`, which
reject bad cron expressions / IANA timezones before they are persisted, so a
typo fails at the API with a clear 400 instead of breaking trigger build deep in
the scheduler sync loop. Validation reuses the exact resolvers APScheduler runs
at runtime (CronTrigger.from_crontab + pytz), so accepted/rejected sets match.
"""

from __future__ import annotations

import pytest

from app.services.scheduler.validation import (
    validate_cron_expression,
    validate_timezone,
)


@pytest.mark.parametrize(
    "cron",
    ["0  * * * *", "15 14 1 * *", "*/5 * * * *", "0 0 12 * MON-FRI", "0 0 * * 0"],
)
def test_valid_cron_passes(cron):
    validate_cron_expression(cron)  # should not raise


@pytest.mark.parametrize(
    "cron",
    [
        "99 * * * *",      # minute out of range
        "0 25 * * *",      # hour out of range
        "*/0 * * * *",     # zero step
        "foo",             # wrong field count
        "0 * * *",         # too few fields (4 of 5)
        "not a cron expr", # free text, not a cron
    ],
)
def test_invalid_cron_raises(cron):
    with pytest.raises(ValueError):
        validate_cron_expression(cron)


@pytest.mark.parametrize("cron", [None, "", "   "])
def test_empty_cron_allowed(cron):
    validate_cron_expression(cron)  # optional field; empty is valid


@pytest.mark.parametrize(
    "tz",
    ["UTC", "GMT", "America/New_York", "Europe/London", "Etc/GMT+5", "Asia/Tokyo"],
)
def test_valid_timezone_passes(tz):
    validate_timezone(tz)  # should not raise


@pytest.mark.parametrize(
    "tz",
    ["America/BadZone", "Mars/Phobos", "Foo/Bar", "US/New_York"],
)
def test_invalid_timezone_raises(tz):
    with pytest.raises(ValueError):
        validate_timezone(tz)


@pytest.mark.parametrize("tz", [None, "", "   "])
def test_empty_timezone_raises(tz):
    with pytest.raises(ValueError):
        validate_timezone(tz)


def test_invalid_timezone_message_is_user_friendly():
    with pytest.raises(ValueError, match="Unknown timezone"):
        validate_timezone("America/BadZone")
