from __future__ import annotations

import logging
from typing import Any

import aiosmtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


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
        msg = EmailMessage()
        msg["From"] = smtp_from
        msg["To"] = ", ".join(to_emails)
        msg["Subject"] = subject
        msg.set_content(body)

        if attachment and attachment_filename:
            msg.add_attachment(
                attachment,
                maintype="application",
                subtype=self._get_mime_subtype(attachment_filename),
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
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def _get_mime_subtype(filename: str) -> str:
    """Get MIME subtype from filename extension."""
    if filename.endswith(".pdf"):
        return "pdf"
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        return "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif filename.endswith(".csv"):
        return "csv"
    elif filename.endswith(".html") or filename.endswith(".htm"):
        return "html"
    else:
        return "octet-stream"
