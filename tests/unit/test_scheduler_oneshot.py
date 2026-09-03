"""Tests for the one-shot re-fire fix (B5): past one-shot schedules must not be
re-added on every sync cycle, which would re-execute them repeatedly."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.scheduler.engine import ReportScheduler, is_past_one_shot


def _sched(**kw):
    from types import SimpleNamespace

    base = {
        "id": uuid.uuid4(),
        "name": "s",
        "cron_expression": None,
        "run_at": None,
        "timezone": "UTC",
        "is_active": True,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_is_past_one_shot_true_for_past_run_at():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert is_past_one_shot(_sched(run_at=past)) is True


def test_is_past_one_shot_false_for_future_run_at():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert is_past_one_shot(_sched(run_at=future)) is False


def test_is_past_one_shot_false_when_no_run_at():
    assert is_past_one_shot(_sched(run_at=None)) is False


def test_is_past_one_shot_false_for_recurring_cron():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    # A recurring schedule with a past run_at is not a "past one-shot".
    assert is_past_one_shot(_sched(cron_expression="0 * * * *", run_at=past)) is False


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        class _ScalarResult:
            def all(_self):
                return self._items

        return _ScalarResult()


class _FakeDB:
    def __init__(self, schedules):
        self._schedules = schedules

    async def execute(self, stmt):
        return _FakeResult(self._schedules)


@pytest.mark.asyncio
async def test_sync_schedules_skips_past_one_shots():
    past = _sched(run_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    future = _sched(run_at=datetime.now(timezone.utc) + timedelta(hours=1))
    cron = _sched(cron_expression="0 * * * *")

    sched = ReportScheduler("sqlite://")
    await sched.sync_schedules(_FakeDB([past, future, cron]))

    loaded = {job.id for job in sched.scheduler.get_jobs()}
    # Past one-shot is skipped; future one-shot and recurring cron are loaded.
    assert str(past.id) not in loaded
    assert str(future.id) in loaded
    assert str(cron.id) in loaded


@pytest.mark.asyncio
async def test_sync_schedules_skipping_returns_active_count():
    past = _sched(run_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    cron = _sched(cron_expression="0 * * * *")

    sched = ReportScheduler("sqlite://")
    count = await sched.sync_schedules(_FakeDB([past, cron]))

    # Only the cron schedule counts as an active loaded job.
    assert count == 1
