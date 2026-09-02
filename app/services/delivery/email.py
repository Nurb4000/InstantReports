from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

logger = logging.getLogger(__name__)


async def log_delivery_audit(action: str, report_id: uuid.UUID | None = None, schedule_id: uuid.UUID | None = None, details: dict | None = None):
    """Log delivery audit event to database."""
    try:
        from app.database import async_session_factory
        from app.models.connection import AuditLog
        
        async with async_session_factory() as db:
            audit_entry = AuditLog(
                id=uuid.uuid4(),
                report_id=report_id,
                schedule_id=schedule_id,
                action=action,
                details=details or {},
                executed_at=datetime.now(timezone.utc),
            )
            db.add(audit_entry)
            await db.commit()
    except Exception as e:
        logger.warning(f"Failed to log delivery audit event: {e}")


async def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_from: str,
    to_emails: list[str],
    subject: str,
    body: str,
    attachment: bytes | None = None,
    attachment_filename: str | None = None,
    use_tls: bool = True,
) -> bool:
    """Send an email with optional attachment.

    Args:
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username
        smtp_password: SMTP password
        smtp_from: From address
        to_emails: List of recipient emails
        subject: Email subject
        body: Email body
        attachment: Optional attachment bytes
        attachment_filename: Optional attachment filename
        use_tls: Whether to use TLS

    Returns:
        True if sent successfully, False otherwise
    """
    try:
        import aiosmtplib

        msg = EmailMessage()
        msg["From"] = smtp_from
        msg["To"] = ", ".join(to_emails)
        msg["Subject"] = subject
        msg.set_content(body)

        if attachment and attachment_filename:
            msg.add_attachment(
                attachment,
                maintype="application",
                subtype=_get_mime_subtype(attachment_filename),
                filename=attachment_filename,
            )

        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user if smtp_user else None,
            password=smtp_password if smtp_password else None,
            start_tls=use_tls,
        )

        logger.info(f"Email sent to {to_emails}: {subject}")
        
        # Log audit event for successful delivery
        await log_delivery_audit(
            action="delivery_success",
            details={
                "message": f"Email sent to {', '.join(to_emails)}",
                "subject": subject,
                "recipients": to_emails,
            }
        )
        
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def _get_mime_subtype(filename: str) -> str:
    """Get MIME subtype from filename extension (case-insensitive)."""
    filename = filename.lower()
    if filename.endswith(".pdf"):
        return "pdf"
    elif filename.endswith((".xlsx", ".xls")):
        return "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif filename.endswith(".csv"):
        return "csv"
    elif filename.endswith((".html", ".htm")):
        return "html"
    else:
        return "octet-stream"


async def test_connection(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_from: str,
    use_tls: bool = True,
) -> tuple[bool, str]:
    """Verify SMTP connectivity and authentication without sending any mail.

    Returns a (success, message) tuple.
    """
    if not smtp_host:
        return False, "SMTP host is required"

    try:
        import aiosmtplib

        client = aiosmtplib.AiosMTPClient(
            hostname=smtp_host,
            port=smtp_port,
            start_tls=use_tls,
        )
        await client.connect()
        if smtp_user:
            await client.login(smtp_user, smtp_password)
        await client.quit()

        logger.info(f"SMTP connection test succeeded for {smtp_host}:{smtp_port}")
        return True, "Connected and authenticated successfully"

    except ImportError:
        return False, "aiosmtplib is not installed"
    except Exception as e:
        logger.error(f"SMTP connection test failed for {smtp_host}:{smtp_port}: {e}")
        return False, f"Connection failed: {e}"
