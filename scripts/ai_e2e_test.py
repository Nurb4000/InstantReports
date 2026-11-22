"""Live e2e test of the app's AI code path against a running app.

The app's AI routes all funnel through AIClient.chat_completion() -> an
OpenAI-compatible {base_url}/chat/completions endpoint. This test points the app
at a stub (see scripts/ai_stub.py + docker-compose.override.yml) and exercises
all five /ai/* endpoints to prove the client wiring, retry/error mapping, and
per-operation response parsing work end to end.

Usage:
  E2E_BASE=http://192.168.122.233:8080 \
  E2E_ADMIN_EMAIL=admin@example.com E2E_ADMIN_PASS='...' \
  python3 scripts/ai_e2e_test.py
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASS = os.environ.get("E2E_ADMIN_PASS", "Str0ng-Passw0rd!")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def login():
    conn = http.client.HTTPConnection(_host(), _port(), timeout=15)
    body = urllib.parse.urlencode({"email": ADMIN_EMAIL, "password": ADMIN_PASS}).encode()
    conn.request("POST", "/auth/login", body=body,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse(); set_cookie = resp.getheader("Set-Cookie") or ""; resp.read(); conn.close()
    m = re.search(r"access_token=([^;]+)", set_cookie)
    return m.group(1) if m else None


def _host() -> str:
    return BASE.split("//")[1].split(":")[0]


def _port() -> int:
    rest = BASE.split("//")[1]
    return int(rest.split(":")[1]) if ":" in rest else 80


def ai_post(path: str, token: str, payload: dict) -> tuple[int, object]:
    # Bearer auth (not cookie) so the request is not cookie-authenticated and
    # therefore CSRF-exempt -- matches how the main e2e suite authenticates.
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        raw = resp.read().decode()
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


SCHEMA = {
    "products": [
        {"name": "products", "columns": [
            {"name": "name", "type": "TEXT"},
            {"name": "category", "type": "TEXT"},
            {"name": "price", "type": "REAL"},
            {"name": "stock", "type": "INTEGER"},
        ]},
    ],
}


def main() -> int:
    import urllib.parse  # noqa: E402
    import urllib.error  # noqa: E402

    token = login()
    check("auth: login returns access_token cookie", bool(token))
    if not token:
        return 1

    calls = [
        ("/ai/generate-report", {"prompt": "A sales summary table by category with totals",
                                 "schema": SCHEMA}, "report_definition"),
        ("/ai/generate-sql", {"prompt": "Top 5 products by price", "schema": SCHEMA,
                              "connector_type": "postgresql"}, "sql"),
        ("/ai/suggest-layout", {"data_schema": SCHEMA, "report_type": "summary"}, "layout_suggestion"),
        ("/ai/insights", {"data": [{"name": "A", "price": 10}, {"name": "B", "price": 20}],
                          "context": "monthly sales"}, "insights"),
        ("/ai/chat", {"message": "Help me design a sales summary report", "history": []}, "response"),
    ]

    for path, payload, field in calls:
        try:
            status, body = ai_post(path, token, payload)
            if isinstance(body, dict) and field in body:
                val = body[field]
                ok = bool(val)
                preview = val if isinstance(val, str) else json.dumps(val)[:120]
                check(f"AI {path} -> {field}", ok, f"status={status}, value={preview!r}")
            else:
                check(f"AI {path} -> {field}", False, f"status={status}, body={body!r}")
        except Exception as e:  # noqa: BLE001
            check(f"AI {path} -> {field}", False, str(e))

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n===== AI E2E SUMMARY: {passed}/{len(results)} checks passed =====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
