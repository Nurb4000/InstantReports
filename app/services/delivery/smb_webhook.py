from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _sign_webhook(secret: str, timestamp: str, body: bytes) -> str:
    """Compute the Stripe-convention webhook signature.

    The ``X-Webhook-Signature`` header advertises ``t=<ts>,v1=<sig>``, so the
    signed message must be ``"<ts>.<body>"`` (not the body alone). Signing only
    the body lets an attacker replay a captured payload with a fresh timestamp,
    and receivers implementing the advertised convention reject body-only
    signatures outright.
    """
    import hashlib
    import hmac

    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


async def send_smb(
    server: str,
    share: str,
    username: str,
    password: str,
    remote_path: str = "/",
    file_data: bytes | None = None,
    filename: str | None = None,
) -> bool:
    """Send a file via SMB (Windows UNC path).

    Args:
        server: SMB server hostname
        share: SMB share name
        username: SMB username
        password: SMB password
        remote_path: Remote directory path within share
        file_data: File data bytes
        filename: Remote filename

    Returns:
        True if successful, False otherwise
    """
    try:
        import smbclient
        import smbprotocol

        smb_url = f"\\\\{server}\\{share}"
        remote_file = f"{smb_url}{remote_path.rstrip('/')}/{filename}" if filename else f"{smb_url}{remote_path}"

        # smbclient / smbprotocol are synchronous; run them in a thread pool so
        # the event loop is not blocked for the entire transfer.
        def _do_smb():
            smbclient.register_session(smb_url, username=username, password=password)
            with smbprotocol.open_file(remote_file, mode="wb") as f:
                f.write(file_data)

        await asyncio.to_thread(_do_smb)

        logger.info(f"SMB file sent to {remote_file}")
        return True

    except Exception as e:
        logger.error(f"Failed to send SMB file: {e}")
        return False


async def send_webhook(
    url: str,
    payload: dict[str, Any],
    secret: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> bool:
    """Send a webhook with optional HMAC signing.

    Args:
        url: Webhook URL
        payload: JSON payload to send
        secret: Optional HMAC secret for signing
        headers: Optional additional headers
        timeout: Request timeout in seconds

    Returns:
        True if successful, False otherwise
    """
    try:
        from datetime import datetime, timezone

        import httpx

        request_headers = headers or {}
        request_headers["Content-Type"] = "application/json"

        body = __import__("json").dumps(payload).encode()

        if secret:
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            signature = _sign_webhook(secret, timestamp, body)
            request_headers["X-Webhook-Signature"] = f"t={timestamp},v1={signature}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=body, headers=request_headers)
            response.raise_for_status()

        logger.info(f"Webhook sent to {url}: {response.status_code}")
        return True

    except Exception as e:
        logger.error(f"Failed to send webhook: {e}")
        return False


async def test_smb_connection(
    server: str,
    share: str,
    username: str,
    password: str,
    remote_path: str = "/",
) -> tuple[bool, str]:
    """Verify SMB connectivity and authentication without writing any files.

    Returns a (success, message) tuple.
    """
    if not server or not share or not username:
        return False, "Server, share, and username are required"

    try:
        import smbclient
        import smbprotocol

        smb_url = f"\\\\{server}\\{share}"
        smbclient.register_session(smb_url, username=username, password=password)

        remote_file = f"{smb_url}{remote_path.rstrip('/')}/."
        with smbprotocol.open_file(remote_file, mode="rb") as f:
            f.read(0)

        logger.info(f"SMB connection test succeeded for {smb_url}")
        return True, "Connected and authenticated successfully"

    except ImportError:
        return False, "smbprotocol/smbclient are not installed"
    except Exception as e:
        logger.error(f"SMB connection test failed for {server}/{share}: {e}")
        return False, f"Connection failed: {e}"


async def test_webhook_connection(
    url: str,
    secret: str | None = None,
    timeout: int = 10,
) -> tuple[bool, str]:
    """Verify a webhook endpoint is reachable by sending a lightweight probe.

    Returns a (success, message) tuple.
    """
    if not url:
        return False, "A webhook URL is required"

    try:
        from datetime import datetime, timezone

        import httpx

        payload = {"test": True, "timestamp": datetime.now(timezone.utc).isoformat()}
        body = __import__("json").dumps(payload).encode()

        request_headers = {"Content-Type": "application/json"}
        if secret:
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            signature = _sign_webhook(secret, timestamp, body)
            request_headers["X-Webhook-Signature"] = f"t={timestamp},v1={signature}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=body, headers=request_headers)
            response.raise_for_status()

        logger.info(f"Webhook connection test succeeded for {url}: {response.status_code}")
        return True, f"Endpoint reachable (HTTP {response.status_code})"

    except Exception as e:
        logger.error(f"Webhook connection test failed for {url}: {e}")
        return False, f"Connection failed: {e}"
