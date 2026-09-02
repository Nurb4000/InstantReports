from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date_trigger import DateTrigger

logger = logging.getLogger(__name__)


async def log_audit(db, action: str, report_id: uuid.UUID | None = None, schedule_id: uuid.UUID | None = None, details: dict | None = None, output_id: uuid.UUID | None = None):
    """Log an audit event to the database."""
    from app.database import get_db
    from app.models.connection import AuditLog
    
    async for db_session in get_db():
        audit_entry = AuditLog(
            id=uuid.uuid4(),
            report_id=report_id,
            schedule_id=schedule_id,
            action=action,
            details=details or {},
            output_id=output_id,
            executed_at=datetime.now(timezone.utc),
        )
        db_session.add(audit_entry)
        await db_session.commit()
        break


class ReportScheduler:
    """APScheduler-based scheduler for report execution."""

    def __init__(self, database_url: str):
        self.scheduler = AsyncIOScheduler()
        self.jobstores = {
            "default": SQLAlchemyJobStore(url=database_url)
        }
        self.scheduler.configure(jobstores=self.jobstores)

    def start(self) -> None:
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("Report scheduler started")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self.scheduler.shutdown()
        logger.info("Report scheduler shut down")

    def add_schedule(
        self,
        job_id: str,
        cron_expression: str | None = None,
        run_at: datetime | None = None,
        timezone: str = "UTC",
    ) -> None:
        """Add a schedule job.

        Args:
            job_id: Unique job identifier
            cron_expression: Cron expression for recurring schedules
            run_at: Specific datetime for one-shot schedules
            timezone: Timezone for the schedule
        """
        if cron_expression:
            trigger = CronTrigger.from_crontab(cron_expression, timezone=timezone)
        elif run_at:
            trigger = DateTrigger(run_date=run_at, timezone=timezone)
        else:
            raise ValueError("Either cron_expression or run_at must be provided")

        self.scheduler.add_job(
            self._execute_report,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )
        logger.info(f"Added schedule job: {job_id}")

    def remove_schedule(self, job_id: str) -> None:
        """Remove a schedule job."""
        self.scheduler.remove_job(job_id)
        logger.info(f"Removed schedule job: {job_id}")

    def pause_schedule(self, job_id: str) -> None:
        """Pause a schedule job."""
        self.scheduler.pause_job(job_id)
        logger.info(f"Paused schedule job: {job_id}")

    def resume_schedule(self, job_id: str) -> None:
        """Resume a paused schedule job."""
        self.scheduler.resume_job(job_id)
        logger.info(f"Resumed schedule job: {job_id}")

    async def _execute_report(self, *args: Any, **kwargs: Any) -> None:
        """Execute a report (to be overridden by the runner)."""
        logger.info("Report execution triggered")
        
        # Log audit event for schedule execution
        job_id = kwargs.get('job_id') or args[0] if args else None
        if job_id:
            try:
                await log_audit(
                    db=None,  # Will be passed from runner
                    action="schedule_executed",
                    schedule_id=uuid.UUID(job_id) if job_id else None,
                    details={"message": "Schedule execution started"}
                )
            except Exception as e:
                logger.warning(f"Failed to log audit event: {e}")
