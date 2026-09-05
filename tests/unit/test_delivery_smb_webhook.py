"""Unit tests for SMB + webhook delivery (``smb_webhook.py``).

These two delivery methods had no test coverage. Webhook delivery is pure logic on top of
``httpx`` (installed natively); SMB delivery imports ``smbclient`` / ``smbprotocol`` which are
*not* installed here, so both are exercised by injecting fakes into ``sys.modules`` — the same
technique used by ``test_delivery_sftp.py``.

The most important thing under test is the HMAC webhook signature: a wrong/missing signature makes
every signed webhook delivery fail at the receiver, and the broad ``except`` in ``send_webhook``
turns that failure into a silent ``False`` with only a log line, so this path deserves direct
coverage.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
import types
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.delivery.smb_webhook import (
    send_smb,
    send_webhook,
)

# --------------------------------------------------------------------------- #
# Webhook (httpx) fakes
# --------------------------------------------------------------------------- #


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpClient:
    """Stand-in for ``httpx.AsyncClient`` that records the last request."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.last_request: dict[str, object] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, content: bytes | None = None, headers: dict | None = None):
        self.last_request = {"url": url, "content": content, "headers": headers}
        return _FakeHttpResponse(self.status_code)


def _inject_fake_httpx(
    monkeypatch: pytest.MonkeyPatch, status_code: int = 200
) -> _FakeHttpClient:
    client = _FakeHttpClient(status_code=status_code)
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.AsyncClient = lambda *a, **k: client
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    return client


def _expected_signature(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_send_webhook_sets_json_content_type(monkeypatch):
    client = _inject_fake_httpx(monkeypatch)

    ok = asyncio.run(send_webhook("https://example.com/hook", {"a": 1}))

    assert ok is True
    headers = client.last_request["headers"]  # type: ignore[union-attr]
    assert headers["Content-Type"] == "application/json"


def test_send_webhook_sends_json_encoded_body(monkeypatch):
    client = _inject_fake_httpx(monkeypatch)

    asyncio.run(send_webhook("https://example.com/hook", {"report_id": "abc", "n": 2}))

    body = client.last_request["content"]  # type: ignore[union-attr]
    import json

    assert json.loads(body) == {"report_id": "abc", "n": 2}


def test_send_webhook_signs_payload_with_hmac(monkeypatch):
    client = _inject_fake_httpx(monkeypatch)
    secret = "top-secret"

    ok = asyncio.run(send_webhook("https://example.com/hook", {"x": 1}, secret=secret))

    assert ok is True
    headers = client.last_request["headers"]  # type: ignore[union-attr]
    body = client.last_request["content"]  # type: ignore[union-attr]
    sig_line = headers["X-Webhook-Signature"]  # type: ignore[index]
    parts = dict(pair.split("=", 1) for pair in sig_line.split(","))
    assert parts["v1"] == _expected_signature(secret.encode(), body)
    # timestamp is unix seconds
    assert parts["t"].isdigit()


def test_send_webhook_omits_signature_without_secret(monkeypatch):
    client = _inject_fake_httpx(monkeypatch)

    asyncio.run(send_webhook("https://example.com/hook", {"x": 1}))

    headers = client.last_request["headers"]  # type: ignore[union-attr]
    assert "X-Webhook-Signature" not in headers


def test_send_webhook_merges_custom_headers(monkeypatch):
    client = _inject_fake_httpx(monkeypatch)

    asyncio.run(
        send_webhook("https://example.com/hook", {"x": 1}, headers={"X-Custom": "v"})
    )

    headers = client.last_request["headers"]  # type: ignore[union-attr]
    assert headers["X-Custom"] == "v"
    assert headers["Content-Type"] == "application/json"


def test_send_webhook_returns_false_on_http_error(monkeypatch):
    _inject_fake_httpx(monkeypatch, status_code=500)

    ok = asyncio.run(send_webhook("https://example.com/hook", {"x": 1}))

    assert ok is False


def test_send_webhook_returns_false_when_unreachable(monkeypatch):
    import httpx as real_httpx

    # A client that raises before returning (e.g. connection refused) -> swallowed to False.
    def boom(*_a: object, **_k: object) -> MagicMock:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=cm)
        cm.__aexit__ = AsyncMock(return_value=False)
        cm.post = AsyncMock(side_effect=real_httpx.ConnectError("down"))

        return cm

    fake_httpx = types.ModuleType("httpx")
    fake_httpx.AsyncClient = boom
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    ok = asyncio.run(send_webhook("https://example.com/hook", {"x": 1}))
    assert ok is False


def test_webhook_connection_requires_url(monkeypatch):
    from app.services.delivery.smb_webhook import test_webhook_connection

    monkeypatch.delitem(sys.modules, "httpx", raising=False)
    result = asyncio.run(test_webhook_connection(""))
    assert result == (False, "A webhook URL is required")


def test_webhook_connection_reports_reachable_status(monkeypatch):
    from app.services.delivery.smb_webhook import test_webhook_connection

    client = _inject_fake_httpx(monkeypatch, status_code=204)

    ok, message = asyncio.run(test_webhook_connection("https://example.com/ping"))

    assert ok is True
    assert "204" in message
    assert client.last_request is not None  # type: ignore[unreachable]


# --------------------------------------------------------------------------- #
# SMB (smbclient / smbprotocol) fakes
# --------------------------------------------------------------------------- #


def _inject_fake_smb(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Install fake ``smbclient`` / ``smbprotocol`` modules; return the mocks."""
    reg_session = MagicMock()

    file_handle = MagicMock()
    file_cm = MagicMock()
    file_cm.__enter__ = MagicMock(return_value=file_handle)
    file_cm.__exit__ = MagicMock(return_value=False)

    open_file = MagicMock(return_value=file_cm)

    fake_smbclient = types.ModuleType("smbclient")
    fake_smbclient.register_session = reg_session
    fake_smbprotocol = types.ModuleType("smbprotocol")
    fake_smbprotocol.open_file = open_file

    monkeypatch.setitem(sys.modules, "smbclient", fake_smbclient)
    monkeypatch.setitem(sys.modules, "smbprotocol", fake_smbprotocol)
    return reg_session, open_file, file_handle


def test_send_smb_registers_session_and_writes_file(monkeypatch):
    reg_session, open_file, file_handle = _inject_fake_smb(monkeypatch)

    ok = asyncio.run(
        send_smb(
            server="myserver",
            share="reports",
            username="u",
            password="p",
            remote_path="/invoices/",
            file_data=b"%PDF-1.4 bytes",
            filename="r.pdf",
        )
    )

    assert ok is True
    reg_session.assert_called_once_with(
        "\\\\myserver\\reports", username="u", password="p"
    )
    opened = open_file.call_args.args[0]
    # UNC server\\share + forward-slash remote path (smbprotocol convention)
    assert opened == "\\\\myserver\\reports/invoices/r.pdf"
    assert file_handle.write.call_count == 1


def test_send_smb_defaults_remote_path_to_share_root(monkeypatch):
    _, open_file, _ = _inject_fake_smb(monkeypatch)

    asyncio.run(
        send_smb("srv", "sh", "u", "p", file_data=b"x", filename="f.txt")
    )

    assert open_file.call_args.args[0] == "\\\\srv\\sh/f.txt"


def test_send_smb_returns_false_when_modules_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "smbclient", raising=False)
    monkeypatch.delitem(sys.modules, "smbprotocol", raising=False)
    ok = asyncio.run(send_smb("srv", "sh", "u", "p", file_data=b"x", filename="f"))
    assert ok is False


def test_smb_connection_validates_required_fields():
    from app.services.delivery.smb_webhook import test_smb_connection

    # No fakes injected: must fail fast on the missing-field guard, not the import guard.
    # password has no default, so pass "" (it is not part of the required-fields check).
    result = asyncio.run(test_smb_connection("", "sh", "u", ""))
    assert result == (
        False,
        "Server, share, and username are required",
    )


def test_smb_connection_reports_import_error_separately(monkeypatch):
    from app.services.delivery.smb_webhook import test_smb_connection

    monkeypatch.delitem(sys.modules, "smbclient", raising=False)
    monkeypatch.delitem(sys.modules, "smbprotocol", raising=False)
    ok, message = asyncio.run(test_smb_connection("srv", "sh", "u", "p"))
    assert ok is False
    assert "not installed" in message
