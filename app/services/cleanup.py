from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete

from app.config import settings
from app.database import async_session_factory
from app.models.report import ReportOutput
from app.services.delivery.email import send_email

logger = logging.getLogger(__name__)


async def cleanup_old_outputs(retention_days: int | None = None) -> int:
    """Delete report outputs older than retention period.

    Args:
        retention_days: Number of days to keep (defaults to settings.REPORT_RETENTION_DAYS)

    Returns:
        Number of deleted records
    """
    if retention_days is None:
        retention_days = settings.REPORT_RETENTION_DAYS

    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    async with async_session_factory() as db:
        result = await db.execute(
            delete(ReportOutput).where(ReportOutput.generated_at < cutoff)
        )
        await db.commit()
        deleted_count = result.rowcount

    logger.info(f"Cleaned up {deleted_count} report outputs older than {retention_days} days")
    return deleted_count


async def send_failure_notification(
    schedule_name: str,
    error_message: str,
    notify_emails: list[str] | None = None,
) -> bool:
    """Send failure notification email for a scheduled report.

    Args:
        schedule_name: Name of the failed schedule
        error_message: Error details
        notify_emails: List of email addresses to notify (defaults to SMTP_FROM)

    Returns:
        True if sent successfully, False otherwise
    """
    if not notify_emails:
        notify_emails = [settings.SMTP_FROM] if settings.SMTP_FROM else []

    if not notify_emails:
        logger.warning("No notification emails configured")
        return False

    subject = f"[InstantReports] Schedule Failed: {schedule_name}"
    body = f"""A scheduled report has failed.

Schedule: {schedule_name}
Error: {error_message}
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

Please check the InstantReports logs for more details."""

    success = await send_email(
        smtp_host=settings.SMTP_HOST,
        smtp_port=settings.SMTP_PORT,
        smtp_user=settings.SMTP_USER,
        smtp_password=settings.SMTP_PASSWORD,
        smtp_from=settings.SMTP_FROM,
        to_emails=notify_emails,
        subject=subject,
        body=body,
    )

    return success
