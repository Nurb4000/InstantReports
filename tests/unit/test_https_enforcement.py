"""Tests for HTTPS enforcement + HSTS (feature-gap #6).

Two controls, both gated by settings that default off so local HTTP dev and the
test suite are unaffected unless an operator enables them:

* ``Strict-Transport-Security`` emitted by SecurityHeadersMiddleware when
  ``HTTPS_ENFORCED`` is true.
* An HTTP -> HTTPS 307 redirect by HTTPSRedirectMiddleware when
  ``HTTPS_REDIRECT`` is true (honouring ``X-Forwarded-Proto`` set by a TLS
  terminating proxy).
"""

import pytest_asyncio

from app.config import settings as app_settings


class TestHSTSHeader:
    async def test_absent_by_default(self, client, monkeypatch):
        """No HSTS header unless HTTPS_ENFORCED is enabled."""
        monkeypatch.setattr(app_settings, "HTTPS_ENFORCED", False, raising=True)
        resp = await client.get("/health")
        assert "Strict-Transport-Security" not in resp.headers

    async def test_present_when_enabled(self, client, monkeypatch):
        """HSTS header present with the configured max-age when enabled."""
        monkeypatch.setattr(app_settings, "HTTPS_ENFORCED", True, raising=True)
        monkeypatch.setattr(
            app_settings, "HSTS_MAX_AGE_SECONDS", 3600, raising=True
        )
        resp = await client.get("/health")
        assert resp.headers["Strict-Transport-Security"] == "max-age=3600; includeSubDomains"

    async def test_present_on_json_response(self, client, monkeypatch):
        """HSTS applies to non-HTML responses too (it is not document-specific)."""
        monkeypatch.setattr(app_settings, "HTTPS_ENFORCED", True, raising=True)
        resp = await client.get("/health")
        assert "Strict-Transport-Security" in resp.headers


class TestHttpsRedirect:
    async def _redirect_when_http(self, client, monkeypatch):
        monkeypatch.setattr(app_settings, "HTTPS_REDIRECT", True, raising=True)
        resp = await client.get(
            "/health", headers={"X-Forwarded-Proto": "http"}
        )
        return resp

    async def test_redirects_http_to_https(self, client, monkeypatch):
        """A plain-HTTP request (per X-Forwarded-Proto) is 307'd to HTTPS."""
        resp = await self._redirect_when_http(client, monkeypatch)
        assert resp.status_code == 307
        assert resp.headers["location"].startswith("https://")

    async def test_no_redirect_when_already_https(self, client, monkeypatch):
        """When the proxy reports HTTPS upstream, no redirect is issued."""
        monkeypatch.setattr(app_settings, "HTTPS_REDIRECT", True, raising=True)
        resp = await client.get(
            "/health", headers={"X-Forwarded-Proto": "https"}
        )
        assert resp.status_code == 200
        assert "location" not in resp.headers

    async def test_no_redirect_when_disabled(self, client, monkeypatch):
        """With HTTPS_REDIRECT off the request passes through unchanged."""
        monkeypatch.setattr(app_settings, "HTTPS_REDIRECT", False, raising=True)
        resp = await client.get(
            "/health", headers={"X-Forwarded-Proto": "http"}
        )
        assert resp.status_code == 200
        assert "location" not in resp.headers

    async def test_redirect_preserves_path_and_query(self, client, monkeypatch):
        """The redirect keeps the original path and query string, only swapping scheme."""
        monkeypatch.setattr(app_settings, "HTTPS_REDIRECT", True, raising=True)
        resp = await client.get(
            "/health?x=1", headers={"X-Forwarded-Proto": "http"}
        )
        assert resp.headers["location"] == "https://test/health?x=1"
