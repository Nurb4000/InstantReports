from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

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
        self.database_url = database_url
        self.scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})

    async def sync_schedules(self, db) -> int:
        """Reconcile the in-memory job registry with the schedules table.

        Adds every active schedule (updating its trigger in place via
        ``replace_existing`` so cron/run_at changes take effect) and removes any
        loaded job that no longer maps to an active schedule. This lets the runner
        pick up schedules created or edited while it is running, and stop firing
        for schedules that were deactivated or deleted, without a restart.

        Returns the number of active schedules found in the DB. Schedules whose
        trigger is invalid are skipped rather than aborting the whole sync.
        """
        from app.models.connection import Schedule

        result = await db.execute(select(Schedule).where(Schedule.is_active.is_(True)))
        active_schedules = result.scalars().all()

        active_ids: set[str] = set()
        for schedule in active_schedules:
            try:
                self.add_schedule(
                    job_id=str(schedule.id),
                    cron_expression=schedule.cron_expression,
                    run_at=schedule.run_at,
                    timezone=schedule.timezone or "UTC",
                )
                active_ids.add(str(schedule.id))
            except Exception as exc:
                logger.error(
                    "Skipping schedule '%s' (%s): %s", schedule.name, schedule.id, exc
                )

        # Drop loaded jobs that no longer correspond to an active schedule. Only
        # UUID-shaped job ids are candidates; the runner's own cleanup job and any
        # other non-schedule job are left untouched.
        for job in self.scheduler.get_jobs():
            job_id = job.id
            if job_id.startswith("cleanup_"):
                continue
            try:
                uuid.UUID(job_id)
            except (ValueError, AttributeError, TypeError):
                continue
            if job_id not in active_ids:
                try:
                    self.scheduler.remove_job(job_id)
                    logger.info("Removed stale schedule job %s", job_id)
                except Exception:
                    logger.debug("Could not remove stale job %s", job_id)

        logger.info("Synced %d active schedule(s) into runner", len(active_ids))
        return len(active_ids)

    async def load_schedules(self, db) -> int:
        """Backward-compatible entry point delegating to :meth:`sync_schedules`.

        At startup there are no stale jobs yet, so reconciliation is equivalent to
        the historical "load every active schedule" behavior.
        """
        return await self.sync_schedules(db)

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
            args=[job_id],  # APScheduler 3.x passes only these to the job fn
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
        """Execute the report referenced by a scheduled job.

        Runs inside APScheduler's event loop; opens its own DB session and
        delegates to ``runner.execute_report`` via a lazy import to avoid a
        circular dependency at module load time.
        """
        from app.runner import execute_report

        args_tuple = args if args else ()
        job_id = kwargs.get("job_id") or (args_tuple[0] if args_tuple else None)
        if not job_id:
            logger.warning("Report triggered without a job id; ignoring")
            return

        try:
            schedule_id = uuid.UUID(str(job_id))
        except (ValueError, AttributeError, TypeError):
            logger.error("Invalid schedule id in job: %r", job_id)
            return

        from app.database import async_session_factory
        from app.models.connection import Schedule

        async with async_session_factory() as db:
            result = await db.execute(
                select(Schedule).where(Schedule.id == schedule_id)
            )
            schedule = result.scalar_one_or_none()
            if not schedule:
                logger.error("Schedule %s not found; skipping execution", schedule_id)
                return
            try:
                await execute_report(schedule, db)
            except Exception as exc:
                logger.error("Report execution failed for schedule %s: %s", schedule_id, exc)
