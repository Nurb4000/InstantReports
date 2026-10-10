"""Tests for scheduled-export failure retry logic."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scheduler.engine import ReportScheduler


class TestExecuteReportRetry:
    """Test that failed schedules retry automatically."""

    @pytest.mark.asyncio
    async def test_retries_with_incremented_count(self):
        """On transient failure with max_retries=2, execute_report runs twice."""
        scheduler = ReportScheduler(database_url="sqlite:///:memory:")
        scheduler.scheduler = MagicMock()

        job_id = str(uuid.uuid4())

        attempts = []

        # Mock execute_report to raise on every call
        async def fake_execute_report(*args, **kwargs):
            attempts.append(1)
            raise RuntimeError("Transient failure")

        with (
            patch("app.runner.execute_report", new=fake_execute_report),
            patch("app.services.scheduler.engine._deliver_scheduled", new=AsyncMock()),
            patch("app.services.cleanup.send_failure_notification", new=AsyncMock()) as notify,
            patch("app.database.async_session_factory") as mock_factory,
        ):
            # Mock the async context manager for the DB session; the schedule
            # lookup must return a (truthy) object with a ``name``.
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = MagicMock()
            mock_db.execute.return_value = mock_result
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler._execute_report(
                job_id=job_id,
                max_retries=2,
                retry_delay=0,
            )

            # Should have attempted twice, then notified once (retries exhausted).
            assert len(attempts) == 2
            assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_no_notification_when_retry_succeeds(self):
        """A failure that succeeds on retry must not send a notification."""
        scheduler = ReportScheduler(database_url="sqlite:///:memory:")
        scheduler.scheduler = MagicMock()

        job_id = str(uuid.uuid4())
        output = MagicMock()
        attempts = []

        async def flaky_execute_report(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("Transient failure")
            return output

        with (
            patch("app.runner.execute_report", new=flaky_execute_report),
            patch("app.services.scheduler.engine._deliver_scheduled", new=AsyncMock()) as deliver,
            patch("app.services.cleanup.send_failure_notification", new=AsyncMock()) as notify,
            patch("app.database.async_session_factory") as mock_factory,
        ):
            mock_db = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler._execute_report(
                job_id=job_id, max_retries=3, retry_delay=0
            )

            assert len(attempts) == 2
            deliver.assert_awaited_once()
            notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        """Successful executions do not trigger retry logic."""
        scheduler = ReportScheduler(database_url="sqlite:///:memory:")
        scheduler.scheduler = MagicMock()

        job_id = str(uuid.uuid4())
        output = MagicMock()
        attempts = []

        async def fake_execute_report(*args, **kwargs):
            attempts.append(1)
            return output

        with (
            patch("app.runner.execute_report", new=fake_execute_report),
            patch("app.services.scheduler.engine._deliver_scheduled", new=AsyncMock()),
            patch("app.database.async_session_factory") as mock_factory,
        ):
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = MagicMock()
            mock_db.execute.return_value = mock_result
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler._execute_report(
                job_id=job_id,
                max_retries=1,
            )
            # One attempt, no retry.
            assert len(attempts) == 1
