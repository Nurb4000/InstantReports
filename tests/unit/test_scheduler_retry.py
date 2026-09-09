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
        """On transient failure with max_retries=2, schedule is re-queued once."""
        scheduler = ReportScheduler(database_url="sqlite:///:memory:")
        scheduler.scheduler = MagicMock()

        job_id = str(uuid.uuid4())

        # Mock execute_report to raise on first call
        async def fake_execute_report(*args, **kwargs):
            raise RuntimeError("Transient failure")

        with (
            patch.dict("sys.modules", {"app.runner": MagicMock(execute_report=fake_execute_report)}),
            patch("app.services.scheduler.engine._deliver_scheduled", new=AsyncMock()),
            patch("app.database.async_session_factory") as mock_factory,
        ):
            # Mock the async context manager for the DB session
            mock_db = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler._execute_report(
                job_id=job_id,
                retry_count=0,
                max_retries=2,
            )

            # Should have retried (reschedule_job called once)
            assert scheduler.scheduler.reschedule_job.called
            call_kwargs = scheduler.scheduler.reschedule_job.call_args
            assert call_kwargs[1]["kwargs"]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        """Successful executions do not trigger retry logic."""
        scheduler = ReportScheduler(database_url="sqlite:///:memory:")
        scheduler.scheduler = MagicMock()

        job_id = str(uuid.uuid4())
        output = MagicMock()

        async def fake_execute_report(*args, **kwargs):
            return output

        with (
            patch.dict("sys.modules", {"app.runner": MagicMock(execute_report=fake_execute_report)}),
            patch("app.services.scheduler.engine._deliver_scheduled", new=AsyncMock()),
            patch("app.database.async_session_factory") as mock_factory,
        ):
            mock_db = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler._execute_report(
                job_id=job_id,
                retry_count=0,
                max_retries=1,
            )
            # Should not reschedule on success
            scheduler.scheduler.reschedule_job.assert_not_called()
