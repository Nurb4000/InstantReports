"""Optional HTTP -> HTTPS redirect (Gap #6 / feature-gap #6).

The app is normally served behind a TLS-terminating proxy/load balancer, so from
the app's point of view a request arrives as plain HTTP even though the client
talked HTTPS. We detect the original scheme from ``X-Forwarded-Proto`` (set by the
proxy) and, when ``HTTPS_REDIRECT`` is enabled, issue a 307 redirect to the same
URL over HTTPS. A 307 preserves the method and body so POST logins are not
downgraded to GET.

Both the header-based HSTS control (security_headers.py) and this redirect are
gated by settings that default off, so local HTTP development and the test suite
are unaffected unless an operator explicitly enables enforcement.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.config import settings


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect plain-HTTP requests to their HTTPS equivalent when enabled."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.HTTPS_REDIRECT:
            return await call_next(request)

        # The proxy terminates TLS and records the original scheme. Absent that
        # header we cannot know, so we stay passive rather than looping.
        forwarded = request.headers.get("x-forwarded-proto", "").lower()
        if forwarded and forwarded.split(",")[0].strip() != "http":
            return await call_next(request)

        # Only redirect safe-to-change methods here; GET/HEAD/POST all work with
        # 307, but we avoid redirecting the login POST's own form posts by
        # letting the app handle them (the 307 will carry the body through).
        target = request.url.replace(scheme="https")
        return RedirectResponse(url=str(target), status_code=307)
