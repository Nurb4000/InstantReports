from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.connection import Delivery, DeliveryRecipient, Schedule
from app.models.report import Report, ReportOutput
from app.services.delivery import send_email, send_sftp, send_smb, send_webhook
from app.services.engine.renderer import ReportRenderer
from app.services.exporters.pdf import PDFExporter

logger = logging.getLogger(__name__)


async def execute_report(schedule: Schedule, db: AsyncSession) -> ReportOutput | None:
    """Execute a scheduled report and return the output."""
    try:
        # Log audit event: schedule execution started
        from app.models.connection import AuditLog
        audit_entry = AuditLog(
            id=uuid.uuid4(),
            report_id=schedule.report_id,
            schedule_id=schedule.id,
            action="schedule_executed",
            details={"message": f"Schedule '{schedule.name}' execution started"},
            executed_at=datetime.utcnow(),
        )
        db.add(audit_entry)
        await db.commit()

        report_result = await db.execute(select(Report).where(Report.id == schedule.report_id))
        report = report_result.scalar_one_or_none()

        if not report:
            logger.error(f"Report {schedule.report_id} not found")
            return None

        renderer = ReportRenderer()
        exporter = PDFExporter()

        rendered = renderer.render(report.definition, {})
        pdf_bytes = exporter.export(rendered)

        output = ReportOutput(
            report_id=report.id,
            schedule_id=schedule.id,
            format="pdf",
            file_name=f"{report.name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf",
            file_data=pdf_bytes,
            file_size=len(pdf_bytes),
            mime_type="application/pdf",
            parameters_used=schedule.parameters or {},
        )
        db.add(output)
        await db.commit()
        
        # Log audit event: report generated successfully
        audit_entry = AuditLog(
            id=uuid.uuid4(),
            report_id=report.id,
            schedule_id=schedule.id,
            action="report_generated",
            details={"message": f"Report '{report.name}' generated successfully", "output_id": str(output.id)},
            output_id=output.id,
            executed_at=datetime.utcnow(),
        )
        db.add(audit_entry)
        await db.commit()
        await db.refresh(output)

        logger.info(f"Report {report.name} generated: {output.file_name}")
        return output

    except Exception as e:
        logger.error(f"Failed to execute report: {e}")
        return None


async def deliver_report(
    output: ReportOutput,
    deliveries: list[Delivery],
    recipients: list[DeliveryRecipient],
) -> bool:
    """Deliver a report output via configured delivery methods."""
    success = True

    for delivery in deliveries:
        config = delivery.config
        delivery_type = delivery.delivery_type

        if delivery_type == "email":
            to_emails = [r.email for r in recipients if r.delivery_id == delivery.id]
            if not to_emails:
                continue

            sent = await send_email(
                smtp_host=settings.SMTP_HOST,
                smtp_port=settings.SMTP_PORT,
                smtp_user=settings.SMTP_USER,
                smtp_password=settings.SMTP_PASSWORD,
                smtp_from=settings.SMTP_FROM,
                to_emails=to_emails,
                subject=f"Report: {output.file_name}",
                body="Please find the attached report.",
                attachment=output.file_data,
                attachment_filename=output.file_name,
            )
            if not sent:
                success = False

        elif delivery_type == "sftp":
            sent = await send_sftp(
                host=config.get("host", ""),
                port=config.get("port", 22),
                username=config.get("username", ""),
                password=config.get("password"),
                remote_path=config.get("remote_path", "/"),
                file_data=output.file_data,
                filename=output.file_name,
            )
            if not sent:
                success = False

        elif delivery_type == "smb":
            sent = await send_smb(
                server=config.get("server", ""),
                share=config.get("share", ""),
                username=config.get("username", ""),
                password=config.get("password", ""),
                remote_path=config.get("remote_path", "/"),
                file_data=output.file_data,
                filename=output.file_name,
            )
            if not sent:
                success = False

        elif delivery_type == "webhook":
            payload = {
                "report_id": str(output.report_id),
                "schedule_id": str(output.schedule_id),
                "file_name": output.file_name,
                "format": output.format,
            }
            sent = await send_webhook(
                url=config.get("url", ""),
                payload=payload,
                secret=config.get("secret"),
            )
            if not sent:
                success = False

    return success


async def cleanup_old_outputs(retention_days: int = 90) -> int:
    """Delete report outputs older than retention period."""
    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    async with async_session_factory() as db:
        result = await db.execute(
            delete(ReportOutput).where(ReportOutput.generated_at < cutoff)
        )
        await db.commit()
        return result.rowcount


async def run_scheduler():
    """Main entry point for runner mode."""
    from app.services.scheduler.engine import ReportScheduler

    scheduler = ReportScheduler(settings.DATABASE_URL)
    scheduler.start()

    # Schedule cleanup job to run daily at 2 AM
    from apscheduler.triggers.cron import CronTrigger
    scheduler.scheduler.add_job(
        cleanup_old_reports,
        trigger=CronTrigger(hour=2, minute=0),
        id="cleanup_old_reports",
        replace_existing=True,
    )

    logger.info("Runner mode started with cleanup scheduler")

    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down runner...")
        scheduler.shutdown()


async def cleanup_old_reports():
    """Cleanup job to remove old report outputs."""
    from app.services.cleanup import cleanup_old_outputs
    
    logger.info("Running report output cleanup...")
    try:
        deleted = await cleanup_old_outputs()
        logger.info(f"Cleanup complete: {deleted} records deleted")
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")


if __name__ == "__main__":
    asyncio.run(run_scheduler())
