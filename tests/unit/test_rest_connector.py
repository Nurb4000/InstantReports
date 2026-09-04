"""Native tests for the REST connector against a real local HTTP server.

Validates the non-postgreSQL connector path (B3 follow-up): the preview/schedule
flow resolves the REST connector and calls execute_query, which must hit the
configured URL and parse both a bare JSON list and a {"data": [...]} envelope.
Runs entirely natively — no Docker or external host required.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pandas as pd
import pytest

from app.services.connectors.rest_graphql import GraphQLConnector, RESTAPIConnector


class _Handler(BaseHTTPRequestHandler):
    payload: str = "[]"
    status: int = 200

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload.encode())

    def log_message(self, *args: Any):
        # Silence per-request server logging during tests.
        pass


class _TrackingHandler(_Handler):
    last_query: str = ""

    def do_GET(self):
        path = self.path
        idx = path.find("?")
        _TrackingHandler.last_query = path[idx + 1:] if idx != -1 else ""
        super().do_GET()


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, url


async def _run(config: dict, url: str) -> pd.DataFrame:
    config = {**config, "url": url}
    return await RESTAPIConnector().execute_query(config, "SELECT *", None)


def test_execute_query_returns_list_shape():
    _Handler.payload = json.dumps([{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
    _Handler.status = 200
    server, url = _serve(_Handler)
    try:
        df = asyncio.run(_run({}, url))
    finally:
        server.shutdown()
        server.server_close()
    assert list(df["name"]) == ["A", "B"]


def test_execute_query_returns_envelope_shape():
    _Handler.payload = json.dumps({"data": [{"id": 9, "name": "Z"}]})
    _Handler.status = 200
    server, url = _serve(_Handler)
    try:
        df = asyncio.run(_run({}, url))
    finally:
        server.shutdown()
        server.server_close()
    assert len(df) == 1
    assert df.iloc[0]["name"] == "Z"


def test_execute_query_forwards_parameters_as_params():
    _Handler.payload = json.dumps([])
    _Handler.status = 200
    _TrackingHandler.last_query = ""
    server, url = _serve(_TrackingHandler)
    try:
        asyncio.run(_run({"params": {"region": "N"}}, url))
    finally:
        server.shutdown()
        server.server_close()
    assert "region=N" in _TrackingHandler.last_query


def test_execute_query_raises_on_non_200():
    _Handler.payload = json.dumps({"error": "boom"})
    _Handler.status = 500
    server, url = _serve(_Handler)
    try:
        # The connector raises a bare Exception on non-200; assert it propagates.
        with pytest.raises(Exception, match="API request failed"):
            asyncio.run(_run({}, url))
    finally:
        server.shutdown()
        server.server_close()


class _AuthTrackingHandler(_Handler):
    captured: dict = {}

    def do_GET(self):
        _AuthTrackingHandler.captured = {
            k.lower(): v for k, v in self.headers.items()
        }
        super().do_GET()


async def _run_rest(config: dict, url: str) -> pd.DataFrame:
    return await RESTAPIConnector().execute_query({**config, "url": url}, "SELECT *", None)


def test_rest_bearer_header_is_applied():
    """REST auth is stored as flat keys (auth_type/auth_token); the bearer token must
    reach the server as an Authorization header. Regression: _make_request used to read
    a nested config['auth'] object that the form never populates, so every authenticated
    REST call was sent unauthenticated."""
    _AuthTrackingHandler.captured = {}
    _Handler.payload = json.dumps([])
    _Handler.status = 200
    server, url = _serve(_AuthTrackingHandler)
    try:
        asyncio.run(_run_rest({"auth_type": "bearer", "auth_token": "SECRET123"}, url))
    finally:
        server.shutdown()
        server.server_close()
    assert _AuthTrackingHandler.captured.get("authorization") == "Bearer SECRET123"


def test_rest_basic_header_is_applied():
    import base64

    _AuthTrackingHandler.captured = {}
    _Handler.payload = json.dumps([])
    _Handler.status = 200
    server, url = _serve(_AuthTrackingHandler)
    try:
        asyncio.run(
            _run_rest(
                {"auth_type": "basic", "auth_username": "u", "auth_password": "p"}, url
            )
        )
    finally:
        server.shutdown()
        server.server_close()
    expected = "Basic " + base64.b64encode(b"u:p").decode()
    assert _AuthTrackingHandler.captured.get("authorization") == expected


def test_rest_json_string_headers_are_coerced():
    """The headers textarea is stored as a JSON string; it must be parsed and sent, not
    crash the request."""
    _AuthTrackingHandler.captured = {}
    _Handler.payload = json.dumps([])
    _Handler.status = 200
    server, url = _serve(_AuthTrackingHandler)
    try:
        asyncio.run(_run_rest({"headers": '{"X-Api-Key":"abc"}'}, url))
    finally:
        server.shutdown()
        server.server_close()
    assert _AuthTrackingHandler.captured.get("x-api-key") == "abc"


class _GqlTrackingHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        _GqlTrackingHandler.captured = {
            k.lower(): v for k, v in self.headers.items()
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data": {"users": [{"id": "1", "name": "Ann"}]}}')

    def log_message(self, *args: Any):
        pass


async def _run_graphql(config: dict, query: str, url: str) -> pd.DataFrame:
    return await GraphQLConnector().execute_query({**config, "url": url}, query)


def test_graphql_json_string_headers_do_not_crash():
    """GraphQL stores headers as a JSON textarea string; every request previously raised
    TypeError: 'str' object is not a mapping on **headers. Regression: the string must be
    coerced to a dict and the custom header forwarded."""
    _GqlTrackingHandler.captured = {}
    server, url = _serve(_GqlTrackingHandler)
    try:
        df = asyncio.run(
            _run_graphql({"headers": '{"X-Custom":"yes"}'}, "{ users { id } }", url)
        )
    finally:
        server.shutdown()
        server.server_close()
    assert len(df) == 1
    assert _GqlTrackingHandler.captured.get("x-custom") == "yes"
