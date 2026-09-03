"""Server-side error surfacing tests (part of #5 Improve error messages).

Verifies that app.main's exception handlers return a consistent JSON payload
``{"detail": ..., "status_code": ...}``:

* 4xx client errors surface their real, app-controlled detail in *both* debug
  and production (so toasts show e.g. "Query is required" instead of a generic
  message).
* 5xx internals stay hidden in production and are only revealed when debug is
  enabled, preventing leakage of ``str(exc)`` / tracebacks.

These handlers live in ``app.main``, which transitively imports FastAPI +
jose (via auth), so the tests skip automatically where those deps are absent
(e.g. the minimal dev venv) and run wherever the full stack is installed.
"""

from __future__ import annotations

import json

import pytest


async def _invoke_handler(monkeypatch, *, debug, status_code, detail, generic=False):
    pytest.importorskip("starlette")
    pytest.importorskip("jose")  # app.main -> routes.auth -> jose

    from app import main

    monkeypatch.setattr(main.app_settings, "DEBUG", debug, raising=True)

    if generic:
        response = await main.general_exception_handler(object(), Exception(detail))
    else:
        from starlette.exceptions import HTTPException as StarletteHTTPException

        exc = StarletteHTTPException(status_code=status_code, detail=detail)
        response = await main.http_exception_handler(object(), exc)

    body = json.loads(response.body.decode("utf-8"))
    return response.status_code, body


# --- 4xx client errors: detail is app-controlled and non-sensitive, so it is
#     always surfaced (production and debug alike). ---------------------------

async def test_404_detail_surfaced_in_production(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=False, status_code=404, detail="Connection not found"
    )
    assert status == 404
    assert body["detail"] == "Connection not found"
    assert body["status_code"] == 404


async def test_400_detail_surfaced_in_debug(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=True, status_code=400, detail="Query is required"
    )
    assert status == 400
    assert body["detail"] == "Query is required"


async def test_401_detail_surfaced_in_production(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=False, status_code=401, detail="Not authenticated"
    )
    assert status == 401
    assert body["detail"] == "Not authenticated"


# --- 5xx HTTPException: internals hidden in production, shown in debug. ------

async def test_500_httpexception_hidden_in_production(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=False, status_code=500, detail="leaky internal str"
    )
    assert status == 500
    assert body["status_code"] == 500
    assert "leaky internal str" not in body["detail"]


async def test_500_httpexception_shown_in_debug(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=True, status_code=500, detail="boom detail"
    )
    assert status == 500
    assert body["detail"] == "boom detail"


# --- Generic (uncaught) exceptions: always 500, internals hidden in prod. ----

async def test_general_exception_hidden_in_production(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=False, status_code=500, detail="leaky internal", generic=True
    )
    assert status == 500
    assert body["status_code"] == 500
    assert "leaky internal" not in body["detail"]


async def test_general_exception_shape_in_debug(monkeypatch):
    status, body = await _invoke_handler(
        monkeypatch, debug=True, status_code=500, detail="raw error", generic=True
    )
    assert status == 500
    assert body["status_code"] == 500
    assert body["detail"] == "raw error"
