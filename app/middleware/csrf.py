"""CSRF protection for cookie-authenticated web requests (H4).

Authentication is cookie-based: a successful login sets an ``access_token``
cookie, and state-changing endpoints resolve the user from that cookie. A
cross-site attacker who tricks a logged-in victim's browser into making such a
request gets the cookie sent automatically, so ``SameSite=lax`` alone is not
enough (it does not protect all state-changing methods).

This middleware implements the synchronizer-token pattern on top of ``SameSite``:

* A high-entropy ``csrf_token`` value is ensured for every browser session
  (generated if absent) and carried in a cookie.
* The same value is injected into server-rendered forms (hidden field) and into
  a ``<meta>`` tag read by client JS to set the ``X-CSRF-Token`` header on all
  htmx/fetch requests.
* Every state-changing request that is not otherwise exempt must present that
  token (via the ``X-CSRF-Token`` header or a ``csrf_token`` form field) matching
  the cookie. Mismatches are rejected with 403 using a constant-time compare.

Exemptions (requests that are not CSRF-vulnerable):

* Safe methods (GET/HEAD/OPTIONS/TRACE).
* ``/auth/login`` (no session yet), ``/health`` and static assets.
* Requests carrying an ``Authorization`` (Bearer JWT) or ``X-API-Key`` header —
  those authenticate via a header the attacker cannot steal with CSRF, so they
  are not cookie-authenticated and need no CSRF token.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from typing import Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

# Per-request CSRF token, exposed to templates through a Jinja global so forms
# and the <meta> tag can embed it without per-route plumbing.
_csrf_token_var: ContextVar[str] = ContextVar("csrf_token")


def get_csrf_token() -> str:
    """Return the current request's CSRF token (for template injection)."""
    return _csrf_token_var.get()


def _is_exempt_path(path: str) -> bool:
    return path in ("/auth/login", "/health") or path.startswith("/static")


def _has_header_auth(request: Request) -> bool:
    """True if the request authenticates via a header (not a cookie)."""
    return bool(request.headers.get("Authorization")) or bool(
        request.headers.get("X-API-Key")
    )


async def _extract_form_token(request: Request) -> str | None:
    """Read ``csrf_token`` from a form-encoded POST body.

    Starlette caches the parsed body/form, so reading it here does not starve
    the downstream endpoint of its payload.
    """
    if request.method != "POST":
        return None
    try:
        form = await request.form()
    except Exception:
        return None
    value = form.get("csrf_token")
    return value.strip() if isinstance(value, str) else None


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Always expose a token to templates (forms + <meta>), even when
        # enforcement is disabled, so rendering never depends on the flag.
        token = request.cookies.get("csrf_token") or secrets.token_urlsafe(32)
        _csrf_token_var.set(token)

        if not settings.CSRF_ENABLED:
            return await call_next(request)

        exempt = (
            request.method in _SAFE_METHODS
            or _is_exempt_path(request.url.path)
            or _has_header_auth(request)
        )

        if not exempt and request.method in _STATE_CHANGING:
            provided = request.headers.get("X-CSRF-Token")
            if provided is None:
                provided = await _extract_form_token(request)
            provided = provided.strip() if isinstance(provided, str) else None
            if not provided or not secrets.compare_digest(provided, token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or mismatched"},
                )

        response = await call_next(request)

        # Set the cookie only when it was freshly generated for this session.
        if request.cookies.get("csrf_token") != token:
            response.set_cookie(
                "csrf_token",
                token,
                httponly=False,  # client JS reads it to set the X-CSRF-Token header
                secure=settings.COOKIE_SECURE,
                max_age=60 * 60 * 8,
                samesite="lax",
                path="/",
            )
        return response
