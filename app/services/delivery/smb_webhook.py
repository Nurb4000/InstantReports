from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def _parse_allowed_networks() -> list:
    """Parse ``ALLOWED_PRIVATE_RANGES`` into a list of networks.

    Returns an empty list when the setting is blank. Each entry lets an
    operator explicitly permit otherwise-internal addresses (e.g.
    ``10.0.0.0/8``) for outbound calls.
    """
    raw = __import__("app.config", fromlist=["settings"]).settings.ALLOWED_PRIVATE_RANGES
    networks: list = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part))
        except ValueError as exc:
            raise ValueError(f"Invalid ALLOWED_PRIVATE_RANGES entry: {part!r}") from exc
    return networks


def _validate_ip_address(ip_str: str, allowed_networks: list) -> None:
    """Fail-closed check for a single resolved IP against the SSRF policy.

    Always permits loopback (local dev/test servers) and public/globally
    routable addresses (public APIs). Any other internal address — private,
    link-local (e.g. ``169.254.169.254`` cloud metadata), reserved, or
    multicast — is rejected unless it falls within an explicitly listed
    ``ALLOWED_PRIVATE_RANGES`` entry.
    """
    ip = ipaddress.ip_address(ip_str)
    if ip.is_loopback:
        return

    is_public = not (
        ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
    if is_public:
        return

    if allowed_networks and any(ip in net for net in allowed_networks):
        return

    raise ValueError(f"URL resolves to a blocked internal address: {ip}")


def _resolve_host(host: str, allowed_networks: list) -> None:
    """Resolve ``host`` via DNS and validate every returned address.

    Resolving here is what closes the SSRF gap: the previous implementation
    inspected only bare-IP literals and returned early for hostnames, so a
    hostname pointing at an internal service (metadata endpoint, loopback,
    10.x/192.168.x, ...) bypassed the guard entirely. Fails closed on any DNS
    error rather than letting the request through.
    """
    try:
        infos = socket.getaddrinfo(host, 0, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, socket.error, UnicodeError) as exc:
        raise ValueError(f"Could not resolve host {host!r}: {exc}") from exc

    seen: set = set()
    for info in infos:
        ip_str = info[4][0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        _validate_ip_address(ip_str, allowed_networks)


def _validate_outbound_url(url: str) -> None:
    """Validate an outbound URL to mitigate SSRF / NTLM capture.

    * Only ``http`` and ``https`` schemes are accepted.
    * The host is resolved via DNS and **every** resolved address is checked
      against the SSRF policy, so a hostname pointing at an internal service
      (metadata endpoint, loopback, 10.x/192.168.x, ...) is rejected just like
      a bare private IP. Previously only bare-IP literals were inspected and
      hostnames bypassed the check entirely.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Scheme must be http or https, got: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise ValueError("URL missing hostname")

    _resolve_host(host, _parse_allowed_networks())


class ValidatingTransport(httpx.AsyncBaseTransport):
    """Async httpx transport that re-validates every request URL.

    Wraps an inner transport and runs :func:`_validate_outbound_url` on each
    request *before* it is sent. Because httpx re-invokes the transport for
    each redirect, this also blocks redirects to internal addresses (e.g. a
    public endpoint that 302s to ``http://169.254.169.254``), which a single
    pre-request check cannot catch. Mounted on both http:// and https://.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        validator: Callable[[str], None] = _validate_outbound_url,
    ) -> None:
        self._inner = inner
        self._validator = validator

    async def handle_async_request(self, request):
        self._validator(str(request.url))
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def make_validating_client(**kwargs) -> httpx.AsyncClient:
    """Build an ``AsyncClient`` whose transport validates every outbound URL.

    ``kwargs`` are forwarded to the underlying ``AsyncHTTPTransport`` (e.g.
    ``timeout=...``). The validating wrapper is mounted for both schemes so
    redirects are covered too. ``trust_env=False`` keeps an attacker who can
    set ``HTTP_PROXY``/``HTTPS_PROXY`` from redirecting traffic past the guard.
    """
    validating = ValidatingTransport(httpx.AsyncHTTPTransport())
    # A single default transport covers both http and https (and every
    # redirect hop), so use ``transport=`` rather than the per-scheme ``mount``
    # API which differs across httpx versions.
    client = httpx.AsyncClient(transport=validating, trust_env=False, **kwargs)
    return client


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

        _validate_outbound_url(url)

        request_headers = headers or {}
        request_headers["Content-Type"] = "application/json"

        body = __import__("json").dumps(payload).encode()

        if secret:
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            signature = _sign_webhook(secret, timestamp, body)
            request_headers["X-Webhook-Signature"] = f"t={timestamp},v1={signature}"

        # Validating transport re-checks the URL (and any redirect) at send time.
        async with make_validating_client(timeout=timeout) as client:
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

        _validate_outbound_url(url)

        payload = {"test": True, "timestamp": datetime.now(timezone.utc).isoformat()}
        body = __import__("json").dumps(payload).encode()

        request_headers = {"Content-Type": "application/json"}
        if secret:
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            signature = _sign_webhook(secret, timestamp, body)
            request_headers["X-Webhook-Signature"] = f"t={timestamp},v1={signature}"

        async with make_validating_client(timeout=timeout) as client:
            response = await client.post(url, content=body, headers=request_headers)
            response.raise_for_status()

        logger.info(f"Webhook connection test succeeded for {url}: {response.status_code}")
        return True, f"Endpoint reachable (HTTP {response.status_code})"

    except Exception as e:
        logger.error(f"Webhook connection test failed for {url}: {e}")
        return False, f"Connection failed: {e}"
