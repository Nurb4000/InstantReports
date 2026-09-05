"""Tests for the OpenAI-compatible AI client.

Two things are covered without hitting a network:

* ``_extract_json_response`` — the helper that recovers JSON from model output
  that is wrapped in markdown code fences (```` ```json ````) or prose. Local /
  smaller models emit fenced JSON even when asked for raw JSON, so a bare
  ``json.loads`` used to fail AI report/layout generation.
* ``AIClient.chat_completion`` — the instruct-vs-reasoning fallback. Reasoning
  models (DeepSeek-R1, SmolLM3 reasoning, ...) return their output in
  ``reasoning_content`` and leave ``content`` empty; the client must fall back so
  both endpoint styles work.

The httpx dependency is replaced with a tiny fake module so no real network call
is made.
"""

from __future__ import annotations

import json
import types
from typing import Self

import pytest

from app.services.ai import client as ai_client
from app.services.ai.client import (
    AIClient,
    AILLMResponseError,
    AIReportGenerator,
    _extract_json_response,
    classify_ai_error,
)


def _patch_httpx(
    monkeypatch: pytest.MonkeyPatch, payload: dict, timeout: float = 60.0
) -> None:
    class _FakeResp:
        def __init__(self, data: dict) -> None:
            self._data = data

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return self._data

    class _FakeClient:
        def __init__(self, timeout: float | None = None) -> None:
            self._resp = _FakeResp(payload)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, url: str, json: object = None, headers: dict | None = None):
            return self._resp

    fake_httpx = types.ModuleType("httpx")
    fake_httpx.AsyncClient = _FakeClient
    fake_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    monkeypatch.setattr(ai_client, "httpx", fake_httpx)


def _chat_payload(content: str = "", reasoning: str = "") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content, "reasoning_content": reasoning}}]}


# --------------------------------------------------------------------------- #
# JSON extraction (markdown fences / prose wrapping)
# --------------------------------------------------------------------------- #


def test_extract_json_strips_markdown_fence():
    fenced = '```json\n{"a": 1}\n```'
    assert _extract_json_response(fenced) == {"a": 1}


def test_extract_json_plain_valid():
    assert _extract_json_response('  {"a": [1, 2, 3]}  ') == {"a": [1, 2, 3]}


def test_extract_json_extracts_from_surrounding_prose():
    wrapped = "Sure!\n{\n  \"x\": true\n}\nHope that helps"
    assert _extract_json_response(wrapped) == {"x": True}


def test_extract_json_handles_array_fence():
    fenced = "```json\n[1, 2, 3]\n```"
    assert _extract_json_response(fenced) == [1, 2, 3]


def test_extract_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json_response("there is no json here")


# --------------------------------------------------------------------------- #
# chat_completion: instruct vs reasoning fallback
# --------------------------------------------------------------------------- #


async def test_chat_completion_returns_instruct_content(monkeypatch):
    _patch_httpx(monkeypatch, _chat_payload(content="the answer", reasoning="silent thinking"))
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    assert await client.chat_completion([{"role": "user", "content": "q"}]) == "the answer"


async def test_chat_completion_falls_back_to_reasoning_content(monkeypatch):
    # Reasoning models leave `content` empty and populate `reasoning_content`.
    _patch_httpx(monkeypatch, _chat_payload(content="", reasoning="the reasoning answer"))
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    assert await client.chat_completion([{"role": "user", "content": "q"}]) == "the reasoning answer"


async def test_chat_completion_prefers_content_when_both_present(monkeypatch):
    _patch_httpx(
        monkeypatch,
        _chat_payload(content="final", reasoning="chain of thought"),
    )
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    assert await client.chat_completion([{"role": "user", "content": "q"}]) == "final"


async def test_chat_completion_empty_when_no_output(monkeypatch):
    _patch_httpx(monkeypatch, _chat_payload(content="", reasoning=""))
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    assert await client.chat_completion([{"role": "user", "content": "q"}]) == ""


# --------------------------------------------------------------------------- #
# End-to-end generator path with fenced JSON
# --------------------------------------------------------------------------- #


async def test_generate_report_parses_fenced_json(monkeypatch):
    fenced = "```json\n" + json.dumps({"name": "R"}) + "\n```"
    payload = {"choices": [{"message": {"role": "assistant", "content": fenced}}]}
    _patch_httpx(monkeypatch, payload)
    gen = AIReportGenerator(AIClient(base_url="http://example:1/v1", api_key="none", model="m"))
    assert await gen.generate_report("a report") == {"name": "R"}


# --------------------------------------------------------------------------- #
# Retry on transient failures
# --------------------------------------------------------------------------- #


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``httpx`` module with a proper exception hierarchy
    (ConnectError/NetworkError/TimeoutException subclass HTTPError) and return it,
    so tests can reference the fake's exception classes."""
    mod = types.ModuleType("httpx")
    mod.HTTPError = type("HTTPError", (Exception,), {})
    mod.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    mod.TimeoutException = type("TimeoutException", (mod.HTTPError,), {})
    mod.ConnectError = type("ConnectError", (mod.HTTPError,), {})
    mod.NetworkError = type("NetworkError", (mod.HTTPError,), {})
    monkeypatch.setattr(ai_client, "httpx", mod)
    return mod


def _drive_httpx(
    mod: types.ModuleType, plan: list
) -> dict:
    """Wire ``mod.AsyncClient`` to an ordered ``plan``; the last entry repeats.

    Each entry is ``("ok", payload)``, ``("err", status_code)`` or
    ``("raise", ExcClass)``. Returns the shared call counter.
    """
    calls = {"n": 0}

    class _FakeResp:
        def __init__(self, status: int, data: dict) -> None:
            self.status_code = status
            self.text = ""
            self._data = data

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                err = mod.HTTPStatusError("err")
                err.response = self
                raise err

        def json(self) -> dict:
            return self._data

    class _FakeClient:
        def __init__(self, timeout: float | None = None) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, url: str, json: object = None, headers: dict | None = None):
            calls["n"] += 1
            kind, value = plan[min(calls["n"] - 1, len(plan) - 1)]
            if kind == "ok":
                return _FakeResp(200, value)
            if kind == "err":
                return _FakeResp(value, {})
            raise value("boom")

    mod.AsyncClient = _FakeClient
    return calls


async def test_chat_completion_retries_transient_then_succeeds(monkeypatch):
    # Two 503s then success -> client recovers after retries.
    mod = _install_fake_httpx(monkeypatch)
    plan = [("err", 503), ("err", 503), ("ok", _chat_payload(content="recovered"))]
    calls = _drive_httpx(mod, plan)
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    result = await client.chat_completion(
        [{"role": "user", "content": "q"}], retries=3, retry_backoff=0.0
    )
    assert result == "recovered"
    assert calls["n"] == 3


async def test_chat_completion_does_not_retry_4xx(monkeypatch):
    # 401 is a hard client error — must raise immediately, no retries.
    mod = _install_fake_httpx(monkeypatch)
    plan = [("err", 401)]
    calls = _drive_httpx(mod, plan)
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    with pytest.raises(mod.HTTPStatusError):
        await client.chat_completion(
            [{"role": "user", "content": "q"}], retries=3, retry_backoff=0.0
        )
    assert calls["n"] == 1


async def test_chat_completion_gives_up_after_retries(monkeypatch):
    # Persistent 500 exhausts the retry budget then raises.
    mod = _install_fake_httpx(monkeypatch)
    plan = [("err", 500)]
    calls = _drive_httpx(mod, plan)
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    with pytest.raises(mod.HTTPStatusError):
        await client.chat_completion(
            [{"role": "user", "content": "q"}], retries=2, retry_backoff=0.0
        )
    # 1 initial attempt + 2 retries = 3 calls.
    assert calls["n"] == 3


async def test_chat_completion_retries_connection_error(monkeypatch):
    mod = _install_fake_httpx(monkeypatch)
    plan = [("raise", mod.ConnectError), ("ok", _chat_payload(content="after reconnect"))]
    calls = _drive_httpx(mod, plan)
    client = AIClient(base_url="http://example:1/v1", api_key="none", model="m")
    result = await client.chat_completion(
        [{"role": "user", "content": "q"}], retries=2, retry_backoff=0.0
    )
    assert result == "after reconnect"
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# classify_ai_error — route-facing error mapping
# --------------------------------------------------------------------------- #


def test_classify_ai_error_model_output_is_502():
    status, detail = classify_ai_error(AILLMResponseError("bad json"), "report generation")
    assert status == 502
    assert "report generation" in detail


def test_classify_ai_error_transient_is_503(monkeypatch):
    mod = _install_fake_httpx(monkeypatch)
    exc = mod.ConnectError("down")
    status, detail = classify_ai_error(exc, "sql")
    assert status == 503
    assert "sql" in detail


def test_classify_ai_error_other_is_500():
    status, detail = classify_ai_error(RuntimeError("weird"), "insights")
    assert status == 500
    assert "insights" in detail
