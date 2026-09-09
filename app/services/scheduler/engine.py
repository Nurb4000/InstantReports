from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from app.models.report import ReportOutput

logger = logging.getLogger(__name__)


def is_past_one_shot(schedule) -> bool:
    """True for a one-shot (``run_at``) schedule whose run time has passed.

    Recurring schedules (``cron_expression`` set) are never past one-shots. A
    past one-shot must not be re-added during sync: APScheduler fires a past
    ``DateTrigger`` immediately (within misfire grace), so re-adding an
    already-run one-shot on every sync cycle would re-execute it repeatedly.
    """
    if getattr(schedule, "cron_expression", None):
        return False
    run_at = getattr(schedule, "run_at", None)
    return run_at is not None and run_at < datetime.now(timezone.utc)


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
            if is_past_one_shot(schedule):
                logger.debug("Skipping past one-shot schedule %s (%s)", schedule.name, schedule.id)
                continue
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
            kwargs={"retry_count": 0, "max_retries": 1},
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

        Implements automatic retry on transient failures (up to MAX_RETRIES).
        After exhausting retries, sends a failure notification.
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

        # Track retry count via job metadata (APSCHEDULER_JOB_ARGS/kwargs)
        retry_count = kwargs.get("retry_count", 0)
        max_retries = kwargs.get("max_retries", 1)

        async with async_session_factory() as db:
            result = await db.execute(
                select(Schedule).where(Schedule.id == schedule_id)
            )
            schedule = result.scalar_one_or_none()
            if not schedule:
                logger.error("Schedule %s not found; skipping execution", schedule_id)
                return
            try:
                output = await execute_report(schedule, db)
                if output is not None:
                    await _deliver_scheduled(output, schedule_id, db)
            except Exception as exc:
                retry_count += 1
                if retry_count < max_retries:
                    # Re-schedule with incremented retry count
                    logger.warning(
                        "Report execution failed for schedule %s (attempt %d/%d): %s. Retrying...",
                        schedule_id, retry_count, max_retries, exc,
                    )
                    self.scheduler.reschedule_job(
                        job_id=str(schedule_id),
                        trigger="date",
                        run_date=datetime.now(timezone.utc) + timedelta(seconds=30),
                        args=args,
                        kwargs={**kwargs, "retry_count": retry_count, "max_retries": max_retries},
                    )
                else:
                    logger.error("Report execution failed for schedule %s after %d retries: %s", schedule_id, max_retries, exc)
                    # Wire up the failure-notification feature (backlog #9): email the
                    # configured SMTP address when a scheduled run raises. It was
                    # implemented in app.services.cleanup but never called, so scheduled
                    # failures were logged but nobody was notified. Imported lazily to
                    # keep this module's load path decoupled from the cleanup/delivery
                    # stack. A notification failure is logged, not raised, so it cannot
                    # mask the original execution error.
                    from app.services.cleanup import send_failure_notification

                    try:
                        await send_failure_notification(schedule.name, str(exc))
                    except Exception as notify_exc:
                        logger.error(
                            "Failed to send failure notification for schedule %s: %s",
                            schedule_id, notify_exc,
                        )


async def _deliver_scheduled(
    output: ReportOutput, schedule_id: uuid.UUID, db
) -> None:
    """Deliver a freshly generated report to the schedule's configured recipients.

    ``execute_report`` only writes the ``ReportOutput`` row; delivery was never
    wired up, so scheduled reports accumulated in the portal but were never sent
    via email/SFTP/SMB/webhook. Loads active deliveries for this schedule plus
    their recipients and delegates to ``runner.deliver_report``. Reports with no
    delivery config (portal-only schedules) are simply not delivered. Runs inside
    the caller's DB session so ``output.file_data`` (a BYTEA) stays loaded.
    """
    from app.models.connection import Delivery, DeliveryRecipient
    from app.runner import deliver_report

    result = await db.execute(
        select(Delivery).where(
            Delivery.schedule_id == schedule_id,
            Delivery.is_active.is_(True),
        )
    )
    deliveries = list(result.scalars().all())
    if not deliveries:
        return

    delivery_ids = [d.id for d in deliveries]
    recip_result = await db.execute(
        select(DeliveryRecipient).where(
            DeliveryRecipient.delivery_id.in_(delivery_ids)
        )
    )
    recipients = list(recip_result.scalars().all())

    try:
        delivered = await deliver_report(output, deliveries, recipients)
        logger.info(
            "Delivery %s for schedule %s (output %s)",
            "ok" if delivered else "failed",
            schedule_id,
            output.id,
        )
    except Exception as exc:
        logger.error("Delivery failed for schedule %s: %s", schedule_id, exc)
