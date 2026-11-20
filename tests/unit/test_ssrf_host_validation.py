"""SSRF regression tests for C2: outbound URL validation must resolve hostnames
and reject those pointing at internal addresses, not just bare private IPs.

Covers the previous gap where hostnames bypassed the private-range check
entirely (DNS resolution "happened at request time" and was never re-validated).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import pytest

from app.services.delivery.smb_webhook import (
    ValidatingTransport,
    _validate_outbound_url,
    make_validating_client,
)


def _fake_getaddrinfo(host, hosts, port, family=socket.AF_INET, socktype=socket.SOCK_STREAM, proto=0):
    """Return fake getaddrinfo results for a given host -> IP mapping."""
    # Build a list indexed by [4] like the real struct tuple.
    results = []
    for ip in hosts.get(host, []):
        r = [None] * 5
        r[0] = socket.AF_INET
        r[1] = socket.SOCK_STREAM
        r[2] = proto
        r[3] = ""
        r[4] = (ip, 0)
        results.append(r)
    if not results:
        raise socket.gaierror(f"getaddrinfo failed for {host}")
    return results


@pytest.fixture
def fake_dns(monkeypatch):
    _HOSTS: dict[str, list[str]] = {}

    def _fake(host, port, family=socket.AF_INET, socktype=socket.SOCK_STREAM, proto=0):
        return _fake_getaddrinfo(host, _HOSTS, port, family, socktype, proto)

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    return _HOSTS


# --- scheme / host shape -----------------------------------------------------


def test_rejects_non_http_schemes():
    with pytest.raises(ValueError):
        _validate_outbound_url("ftp://example.com/a")
    with pytest.raises(ValueError):
        _validate_outbound_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        _validate_outbound_url("httpx://example.com")


def test_rejects_missing_hostname():
    with pytest.raises(ValueError):
        _validate_outbound_url("not-a-url")


# --- bare IPs (unchanged behavior) -------------------------------------------


def test_allows_loopback_ip():
    _validate_outbound_url("http://127.0.0.1:8080/x")


def test_rejects_metadata_ip():
    with pytest.raises(ValueError):
        _validate_outbound_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_private_ips_by_default():
    for ip in ("10.0.0.5", "192.168.1.10", "172.16.5.4"):
        with pytest.raises(ValueError):
            _validate_outbound_url(f"http://{ip}/")


# --- hostnames (the C2 fix) --------------------------------------------------


def test_hostname_resolving_to_public_is_allowed(fake_dns):
    fake_dns["api.example.com"] = ["93.184.216.34"]
    _validate_outbound_url("https://api.example.com/v1")  # no raise


def test_hostname_resolving_to_metadata_is_rejected(fake_dns):
    fake_dns["metadata.internal"] = ["169.254.169.254"]
    with pytest.raises(ValueError):
        _validate_outbound_url("http://metadata.internal/data")


def test_hostname_resolving_to_private_is_rejected(fake_dns):
    fake_dns["db.local"] = ["10.1.2.3"]
    with pytest.raises(ValueError):
        _validate_outbound_url("http://db.local:5432/")


def test_loopback_hostname_allowed(fake_dns):
    fake_dns["localhost"] = ["127.0.0.1", "::1"]
    _validate_outbound_url("http://localhost:3000/")  # no raise


def test_one_blocked_address_among_many_fails_closed(fake_dns):
    # A hostname resolving to both a public and an internal IP must fail closed.
    fake_dns["mixed.example.com"] = ["8.8.8.8", "169.254.169.254"]
    with pytest.raises(ValueError):
        _validate_outbound_url("http://mixed.example.com/")


def test_dns_failure_fails_closed(fake_dns):
    fake_dns["nope.internal"] = []  # getaddrinfo raises gaierror
    with pytest.raises(ValueError):
        _validate_outbound_url("http://nope.internal/")


# --- explicitly allowed private ranges ---------------------------------------


def test_explicitly_allowed_private_range_ok(monkeypatch, fake_dns):
    monkeypatch.setenv("ALLOWED_PRIVATE_RANGES", "10.0.0.0/8")
    # Reload config so the new setting is picked up.
    import app.config as config

    importlib = __import__("importlib")
    importlib.reload(config)

    fake_dns["tenant.example.com"] = ["10.5.5.5"]
    try:
        _validate_outbound_url("http://tenant.example.com/")  # no raise
    finally:
        importlib.reload(config)


# --- transport / client wiring -----------------------------------------------


def test_validating_transport_calls_validator_before_delegating():
    calls: list[str] = []

    def fake_validator(url: str) -> None:
        calls.append(url)

    order: list[str] = []

    class _FakeInner:
        async def handle_async_request(self, request):
            order.append(f"send:{request.url}")
            return "delegated"

        async def aclose(self):
            order.append("close")

    transport = ValidatingTransport(_FakeInner(), validator=fake_validator)

    class _Req:
        def __init__(self, url):
            self.url = url

    result = _asyncio_run(transport.handle_async_request(_Req("http://x/")))
    assert result == "delegated"
    # Validator runs before the request is delegated to the inner transport.
    assert calls == ["http://x/"]
    assert order == ["send:http://x/"]


def test_make_validating_client_uses_validating_transport():
    client = make_validating_client(timeout=5)
    try:
        # A single default transport covers both schemes and every redirect hop.
        assert isinstance(client._transport, ValidatingTransport)
        # trust_env must be off so env proxies can't bypass the guard.
        assert client.trust_env is False
    finally:
        _asyncio_run(client.aclose())


def _asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
