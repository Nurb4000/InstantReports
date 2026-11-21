"""Unit tests for heavy-endpoint rate limiting (Gap #1).

Covers the shared sliding-window limiter that preview/export/PDF/AI routes depend
on: client-key extraction, allowing requests under the quota, blocking with a
``429`` + ``Retry-After`` once the window is full, the window expiring, and the
disabled (quota 0) path. The limiter clock is fixed so behaviour is deterministic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status

import app.routes.rate_limiting as rl
from app.services.ratelimit import RateLimiter


def _make_request(ip: str = "5.6.7.8", forwarded: str | None = None, real_ip: str | None = None):
    req = MagicMock()
    req.client = MagicMock(host=ip)
    req.headers = {}
    if forwarded is not None:
        req.headers["x-forwarded-for"] = forwarded
    if real_ip is not None:
        req.headers["x-real-ip"] = real_ip
    return req


def _fixed_limiter(max_attempts: int, now: float = 0.0) -> RateLimiter:
    return RateLimiter(max_attempts=max_attempts, window_seconds=60, clock=lambda: now)


# --- client-key extraction -------------------------------------------------


def test_client_key_prefers_forwarded_for_first_hop():
    req = _make_request(ip="9.9.9.9", forwarded="203.0.113.7, 10.0.0.1")
    assert rl._client_key(req) == "203.0.113.7"


def test_client_key_falls_back_to_real_ip():
    req = _make_request(ip="9.9.9.9", real_ip="198.51.100.5")
    assert rl._client_key(req) == "198.51.100.5"


def test_client_key_falls_back_to_socket_host():
    req = _make_request(ip="203.0.113.9")
    assert rl._client_key(req) == "203.0.113.9"


def test_client_key_unknown_when_no_client():
    req = MagicMock()
    req.client = None
    req.headers = {}
    assert rl._client_key(req) == "unknown"


# --- dependency behaviour --------------------------------------------------


def test_under_quota_allows(monkeypatch):
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=3))
    dep = rl.rate_limit_heavy_endpoint()
    # Should not raise for up to `max_attempts` requests.
    for _ in range(3):
        dep(_make_request())


def test_over_quota_raises_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=2))
    dep = rl.rate_limit_heavy_endpoint()
    req = _make_request()
    dep(req)  # record 1
    dep(req)  # record 2 -> window full
    with pytest.raises(HTTPException) as excinfo:
        dep(req)  # 3rd blocked
    assert excinfo.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert excinfo.value.headers.get("Retry-After")


def test_window_expires_allows_again(monkeypatch):
    state = {"t": 0.0}
    limiter = RateLimiter(max_attempts=1, window_seconds=60, clock=lambda: state["t"])
    monkeypatch.setattr(rl, "_heavy_limiter", limiter)
    dep = rl.rate_limit_heavy_endpoint()
    req = _make_request()
    dep(req)  # record
    with pytest.raises(HTTPException):
        dep(req)  # blocked within window
    state["t"] = 61.0  # advance past the 60s window
    dep(req)  # allowed again


def test_disabled_quota_never_raises(monkeypatch):
    monkeypatch.setattr(rl.settings, "HEAVY_ENDPOINT_RATE_LIMIT_ATTEMPTS", 0)
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=1))
    dep = rl.rate_limit_heavy_endpoint()
    req = _make_request()
    # Even far beyond the (disabled) limit, nothing is recorded or blocked.
    for _ in range(10):
        dep(req)


def test_different_clients_have_independent_budgets(monkeypatch):
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=1))
    dep = rl.rate_limit_heavy_endpoint()
    dep(_make_request(ip="10.0.0.1"))  # client A uses its single slot
    with pytest.raises(HTTPException):
        dep(_make_request(ip="10.0.0.1"))  # A blocked
    dep(_make_request(ip="10.0.0.2"))  # B still allowed


def test_factory_returns_a_new_dependency_each_call(monkeypatch):
    monkeypatch.setattr(rl, "_heavy_limiter", _fixed_limiter(max_attempts=1))
    dep_a = rl.rate_limit_heavy_endpoint()
    dep_b = rl.rate_limit_heavy_endpoint()
    assert dep_a is not dep_b
