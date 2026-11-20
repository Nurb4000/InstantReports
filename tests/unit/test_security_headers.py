"""Tests for SecurityHeadersMiddleware (D4).

Covers the security headers added to every response:
  * ``X-Content-Type-Options: nosniff`` on all responses, and
  * a per-request nonce-based Content-Security-Policy on HTML responses whose
    echoed nonce matches the one rendered into inline ``<script>``/``<style>``
    tags in the body.
"""
from __future__ import annotations

import re

CSP_RE = re.compile(r"nonce-([A-Za-z0-9_-]+)")
BODY_NONCE_RE = re.compile(r'nonce="([A-Za-z0-9_-]+)"')


def _csp_nonce(policy: str) -> str:
    match = CSP_RE.search(policy)
    assert match, f"no nonce in CSP: {policy!r}"
    return match.group(1)


def _body_nonces(body: str) -> set[str]:
    return set(BODY_NONCE_RE.findall(body))


async def test_index_has_nosniff(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_index_html_has_nonce_csp(client):
    resp = await client.get("/")
    policy = resp.headers["content-security-policy"]

    # Restrictive defaults plus a per-request nonce.
    assert "default-src 'self'" in policy
    assert "script-src 'self' 'nonce-" in policy
    assert "style-src 'self' 'nonce-" in policy
    assert "frame-ancestors 'none'" in policy


async def test_csp_nonce_matches_body_nonce(client):
    resp = await client.get("/")
    csp_nonce = _csp_nonce(resp.headers["content-security-policy"])

    body_nonces = _body_nonces(resp.text)
    # At least one inline block was tagged, and every tagged nonce is the one
    # echoed in the policy (no stale/mismatched nonces).
    assert body_nonces, "no nonce rendered into the HTML body"
    assert body_nonces == {csp_nonce}


async def test_inline_scripts_and_style_carry_nonce(client):
    resp = await client.get("/")
    body = resp.text

    # Inline <script> blocks are tagged with the nonce...
    inline_script = re.search(r"<script[^>]*nonce=\"[A-Za-z0-9_-]+\"", body)
    assert inline_script, "no inline <script> carries a nonce"


def test_inline_style_and_script_tags_render_nonce():
    # The per-request nonce global is callable (Jinja does not auto-call), so
    # every inline <style>/<script> opener uses ``nonce="{{ security_nonce() }}\"\"`.
    # Render that exact pattern through the app's Jinja env with a known nonce and
    # confirm both tag types carry it verbatim.
    from app.middleware import security_headers as sh

    sh._nonce_var.set("TESTNONCE")
    try:
        import app.main as main

        env = main.app.state.templates.env
        snippet = env.from_string(
            '<style nonce="{{ security_nonce() }}"></style>\n'
            '<script nonce="{{ security_nonce() }}"></script>'
        )
        rendered = snippet.render()
    finally:
        sh.clear_security_nonce()

    assert '<style nonce="TESTNONCE">' in rendered
    assert '<script nonce="TESTNONCE">' in rendered


async def test_external_script_without_nonce(client):
    resp = await client.get("/")
    body = resp.text

    # External assets are loaded via 'self' and must NOT carry a nonce.
    ext = re.search(r'<script src="[^"]*escape\.js"></script>', body)
    assert ext, "external escape.js script not found"
    assert "nonce=" not in ext.group(0), "external script should not carry a nonce"


async def test_nonces_unique_per_request(client):
    first = await client.get("/")
    second = await client.get("/")

    assert _csp_nonce(first.headers["content-security-policy"]) != _csp_nonce(
        second.headers["content-security-policy"]
    )


async def test_json_error_response_has_nosniff_but_no_csp(client):
    # A 404 goes through the JSON error handler (non-HTML). nosniff must still
    # apply, but no CSP nonce policy is added for non-HTML responses.
    resp = await client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in resp.headers
