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
    AIReportGenerator,
    _extract_json_response,
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
