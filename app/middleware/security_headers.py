"""Defense-in-depth response headers (D4).

Adds two controls to every response:

* ``X-Content-Type-Options: nosniff`` -- prevents browsers from MIME-sniffing a
  non-declared content type into executable script, a common vector that turns a
  rendered document into a script execution site.
* A ``Content-Security-Policy`` on HTML documents (the SPA, the portal, and
  exported report HTML) -- restricts where script/style/image resources can be
  loaded from and forbids the page from being framed, limiting the blast radius
  of any injected markup.

The CSP uses a per-request nonce so the app's legitimately inline scripts/styles
(htmx bootstrap, Alpine, per-page behavior) keep working while dynamically
injected inline scripts from an attacker are rejected by the browser. The same
nonce is applied to inline ``<script>``/``<style>`` tags via the ``security_nonce``
Jinja global (see templates).

Only same-origin resources are used by the app (all assets serve from
``/static``; AI calls go through the same-origin ``/ai/*`` proxy), so the policy
is strict without breaking functionality.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_nonce_var: ContextVar[str] = ContextVar("security_nonce")


def get_security_nonce() -> str:
    """Return the current request's CSP nonce (for template injection)."""
    return _nonce_var.get()


def clear_security_nonce() -> None:
    """Reset the per-request nonce context (e.g. between requests/tests)."""
    _nonce_var.set("")


def _build_csp(nonce: str) -> str:
    # default-src 'self' locks everything to same origin. script/style allow the
    # per-request nonce for the app's own inline blocks; images allow data URIs
    # so inlined chart canvases/PNGs render. frame-ancestors 'none' prevents
    # clickjacking; base-uri/form-action 'self' block <base> and cross-origin
    # form injection.
    return (
        "default-src 'self';"
        f"script-src 'self' 'nonce-{nonce}';"
        f"style-src 'self' 'nonce-{nonce}';"
        "img-src 'self' data:"
        ";connect-src 'self';"
        ";form-action 'self';"
        ";base-uri 'self';"
        ";frame-ancestors 'none'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Generate a fresh 128-bit nonce per request; templates read it through
        # the security_nonce Jinja global to tag their inline blocks.
        nonce = secrets.token_urlsafe(16)
        _nonce_var.set(nonce)

        response = await call_next(request)

        # nosniff applies to every response, JSON or HTML.
        response.headers["X-Content-Type-Options"] = "nosniff"

        # CSP only needs to guard documents that the browser will execute: HTML.
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            response.headers["Content-Security-Policy"] = _build_csp(nonce)

        return response
