"""Shared rate limiting for compute-heavy endpoints.

Preview/export/PDF generation and the AI calls are CPU-, memory-, or token-
intensive, so an unauthenticated-or-not attacker can hammer them to exhaust
resources (large-dataset memory blow-ups, AI token cost). This module provides a
single per-client sliding-window limiter, keyed by client IP, that those routes
depend on to return ``429 Too Many Requests`` (with ``Retry-After``) once the
configured quota is exceeded within the window.

Like the login limiter (:mod:`app.routes.auth`), these limiters are per-process by
default, so a multi-worker deployment does not enforce an exact global limit but
still raises the cost of abuse substantially. All limiters are built through
:func:`app.services.ratelimit.build_rate_limiter`, which honours ``RATE_LIMIT_BACKEND``
so a deployment can opt into a shared, restart-surviving disk store; the limiter
is constructed from settings so tests can swap in a controlled clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.config import settings
from app.services.ratelimit import RateLimiter, build_rate_limiter


def _client_key(request: Request) -> str:
    """Best-effort client identifier for rate-limit keys.

    Prefers the proxy-forwarded IP when present (the app sits behind a proxy in
    deployment), then ``X-Real-IP``, then the socket host, so keys track the
    originating client rather than the proxy.
    """
    forward = request.headers.get("x-forwarded-for")
    if forward:
        return forward.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _heavy_rate_limiter() -> RateLimiter:
    """Shared limiter built once from settings.

    Built once and cached so repeated requests do not reconstruct it; settings
    are read at construction time (import/first-call), matching how the login
    limiter is wired. Tests replace :data:`_heavy_limiter` with a controlled
    clock to exercise the 429 path deterministically.
    """
    return build_rate_limiter(
        settings.HEAVY_ENDPOINT_RATE_LIMIT_ATTEMPTS,
        settings.HEAVY_ENDPOINT_RATE_LIMIT_WINDOW_SECONDS,
    )


# Module-level singleton so all heavy endpoints share one per-client budget.
_heavy_limiter = _heavy_rate_limiter()


def _api_key_rate_limiter() -> RateLimiter:
    """Shared limiter for the API-key management endpoints.

    Built once from settings and cached; replaced in tests with a controlled
    clock so the 429 path is deterministic. Keyed by client IP like the heavy
    endpoint limiter.
    """
    return build_rate_limiter(
        settings.API_KEY_RATE_LIMIT_ATTEMPTS,
        settings.API_KEY_RATE_LIMIT_WINDOW_SECONDS,
    )


# Module-level singleton so all API-key endpoints share one per-client budget.
_api_key_limiter = _api_key_rate_limiter()


def rate_limit_api_key_endpoint() -> Callable[[Request], None]:
    """Dependency factory that enforces the API-key-endpoint quota.

    Use as ``Depends(rate_limit_api_key_endpoint())`` on the API-key management
    routes. Raises ``429 Too Many Requests`` (with ``Retry-After``) once the
    per-client window is full, before the handler runs; records the request when
    under the limit. A quota of 0 disables limiting entirely.
    """

    def _dep(request: Request) -> None:
        if settings.API_KEY_RATE_LIMIT_ATTEMPTS <= 0:
            return

        key = _client_key(request)
        retry = _api_key_limiter.retry_after(key)
        if retry > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry)},
            )

        _api_key_limiter.record(key)

    return _dep


def rate_limit_heavy_endpoint() -> Callable[[Request], None]:
    """Dependency factory that enforces the heavy-endpoint quota.

    Use as ``Depends(rate_limit_heavy_endpoint())`` on a compute-heavy route. It
    checks the caller's window first and raises ``429 Too Many Requests`` (with a
    ``Retry-After`` header) before the expensive handler runs. When under the
    limit it records the request so the window fills with legitimate use. A quota
    of 0 disables limiting entirely.
    """

    def _dep(request: Request) -> None:
        if settings.HEAVY_ENDPOINT_RATE_LIMIT_ATTEMPTS <= 0:
            return

        key = _client_key(request)
        retry = _heavy_limiter.retry_after(key)
        if retry > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry)},
            )

        _heavy_limiter.record(key)

    return _dep
