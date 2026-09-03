"""Regression tests for SFTP delivery.

Covers the byte-stream handling in ``send_sftp`` without requiring the real
``asyncssh`` dependency (which is not installed in the minimal test env). A
fake ``asyncssh`` module is injected into ``sys.modules`` so we can assert on
how ``SFTPClient.put_file`` is invoked.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.delivery.sftp import send_sftp


def _inject_fake_asyncssh(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install a minimal fake ``asyncssh`` and return the mock sftp client."""
    sftp_client = MagicMock()
    sftp_client.put_file = AsyncMock()

    sftp_cm = MagicMock()
    sftp_cm.__aenter__ = AsyncMock(return_value=sftp_client)
    sftp_cm.__aexit__ = AsyncMock(return_value=False)

    conn = MagicMock()
    conn.start_sftp_client = MagicMock(return_value=sftp_cm)

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=False)

    fake_asyncssh = types.ModuleType("asyncssh")
    fake_asyncssh.connect = MagicMock(return_value=conn_cm)

    monkeypatch.setitem(sys.modules, "asyncssh", fake_asyncssh)
    return sftp_client


def test_send_sftp_transmits_bytes_as_stream(monkeypatch):
    sftp_client = _inject_fake_asyncssh(monkeypatch)

    result = asyncio.run(
        send_sftp(
            host="sftp.example.com",
            port=22,
            username="ir",
            password="secret",
            remote_path="/reports/",
            file_data=b"%PDF-1.4 fake pdf bytes",
            filename="report.pdf",
        )
    )

    assert result is True
    # put_file must receive a readable binary stream, not raw bytes: passing
    # bytes directly raises inside asyncssh and silently breaks all deliveries.
    src, dst = sftp_client.put_file.call_args.args
    assert hasattr(src, "read")
    assert src.read() == b"%PDF-1.4 fake pdf bytes"
    assert dst.endswith("report.pdf")


def test_send_sftp_missing_credentials_returns_false(monkeypatch):
    # No asyncssh injected: the import fails and we degrade to False.
    monkeypatch.delitem(sys.modules, "asyncssh", raising=False)
    result = asyncio.run(
        send_sftp(host="h", port=22, username="", file_data=b"x", filename="f")
    )
    assert result is False
