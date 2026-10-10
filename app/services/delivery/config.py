"""Delivery config builder and redactor.

Centralises the two operations that touch delivery configuration:
* ``build_delivery_config`` — assembles a schedule's ``delivery_config`` dict
  from form fields based on the selected delivery type (email/sftp/smb/webhook).
* ``redact_delivery_config`` — recursively masks secret fields so read
  endpoints never echo plaintext credentials back to callers.

Both operations are pure (no DB, no I/O) and fully unit-testable.
"""
from __future__ import annotations

from typing import Any

_SECRET_KEYS = frozenset({"password", "secret"})


def build_delivery_config(
    delivery_type: str,
    *,
    emails: str = "",
    sftp_host: str = "",
    sftp_port: str = "",
    sftp_username: str = "",
    sftp_password: str = "",
    sftp_remote_path: str = "",
    smb_server: str = "",
    smb_share: str = "",
    smb_username: str = "",
    smb_password: str = "",
    smb_remote_path: str = "",
    webhook_url: str = "",
    webhook_secret: str = "",
) -> dict[str, Any]:
    """Assemble a ``delivery_config`` dict for the given delivery type.

    Only the fields relevant to the selected ``delivery_type`` are included;
    unrelated keys are omitted so the stored config stays lean.
    """
    config: dict[str, Any] = {"type": delivery_type}

    if delivery_type == "email":
        raw = [e.strip() for e in (emails or "").split(",") if e.strip()]
        config["emails"] = raw
    elif delivery_type == "sftp":
        config.update({
            "host": sftp_host or "",
            "port": int(sftp_port) if sftp_port else 22,
            "username": sftp_username or "",
            "password": sftp_password or "",
            "remote_path": sftp_remote_path or "/",
        })
    elif delivery_type == "smb":
        config.update({
            "server": smb_server or "",
            "share": smb_share or "",
            "username": smb_username or "",
            "password": smb_password or "",
            "remote_path": smb_remote_path or "/",
        })
    elif delivery_type == "webhook":
        config.update({
            "url": webhook_url or "",
            "secret": webhook_secret or "",
        })

    return config


_VALID_DELIVERY_TYPES = ("email", "sftp", "smb", "webhook")


async def sync_schedule_deliveries(db, schedule) -> None:
    """Reconcile a schedule's normalized ``Delivery``/``DeliveryRecipient`` rows.

    The schedule create/update routes persist delivery settings on the
    ``Schedule`` row (``delivery_type``/``delivery_config``/``recipient_emails``),
    but the scheduler's delivery path (``_deliver_scheduled`` -> ``deliver_report``)
    reads normalized ``Delivery``/``DeliveryRecipient`` rows. Previously nothing
    ever created those rows, so every scheduled report was generated but never
    delivered. This rebuilds them from the schedule's fields (delete + recreate,
    so updates reconcile without duplicating). Must be followed by a commit.
    """
    from sqlalchemy import delete, select

    from app.models.connection import Delivery, DeliveryRecipient

    existing_ids = list(
        (
            await db.execute(
                select(Delivery.id).where(Delivery.schedule_id == schedule.id)
            )
        )
        .scalars()
        .all()
    )
    if existing_ids:
        await db.execute(
            delete(DeliveryRecipient).where(
                DeliveryRecipient.delivery_id.in_(existing_ids)
            )
        )
        await db.execute(delete(Delivery).where(Delivery.id.in_(existing_ids)))
        await db.flush()

    delivery_type = (schedule.delivery_type or "").strip().lower()
    if delivery_type not in _VALID_DELIVERY_TYPES or not schedule.delivery_config:
        return

    delivery = Delivery(
        schedule_id=schedule.id,
        delivery_type=delivery_type,
        config=schedule.delivery_config,
        is_active=True,
    )
    db.add(delivery)
    await db.flush()

    if delivery_type == "email":
        emails = [
            addr.strip()
            for addr in (schedule.recipient_emails or "").split(",")
            if addr.strip()
        ]
        for email in emails:
            db.add(DeliveryRecipient(delivery_id=delivery.id, email=email))


def redact_delivery_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of ``config`` with secret fields masked.

    Read endpoints (list/get schedule) must not echo plaintext SFTP/SMB
    ``password`` or webhook ``secret`` back to callers; the create/update
    flows still accept them for storage, but responses should never surface
    them.
    """
    if not config:
        return config

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ("***REDACTED***" if key in _SECRET_KEYS else _walk(val))
                for key, val in value.items()
            }
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(config)
