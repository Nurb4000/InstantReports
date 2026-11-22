"""Verify the app's AI client retries past a transient (503) upstream failure.

The stub returns HTTP 503 on the first "flaky" request and 200 afterwards. The
app's AIClient treats 503 as transient and retries, so generate-sql should still
succeed. This exercises the retry/backoff path live against the deployed app.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://192.168.122.233:8080")
EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
PASS = os.environ.get("E2E_ADMIN_PASS", "E2e-VM-Secure-2024")


def login():
    u = urllib.parse.urlparse(BASE)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=15)
    conn.request("POST", "/auth/login",
                 body=urllib.parse.urlencode({"email": EMAIL, "password": PASS}).encode(),
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse(); sc = resp.getheader("Set-Cookie") or ""; resp.read(); conn.close()
    m = re.search(r"access_token=([^;]+)", sc)
    return m.group(1) if m else None


def main() -> int:
    token = login()
    if not token:
        print("[FAIL] login"); return 1
    req = urllib.request.Request(f"{BASE}/ai/generate-sql", data=json.dumps({
        "prompt": "flaky generate sql for top products by price",
        "schema": {"products": [{"name": "products", "columns": [{"name": "name"}]}]},
        "connector_type": "postgresql",
    }).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        body = json.loads(resp.read().decode())
        sql = body.get("sql", "")
        ok = bool(sql and "SELECT" in sql.upper())
        print(f"[{'PASS' if ok else 'FAIL'}] generate-sql retried past 503 -> {sql!r}")
        return 0 if ok else 1
    except urllib.error.HTTPError as e:
        print(f"[FAIL] generate-sql returned {e.code} after retries: {e.read().decode()[:200]}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {e}"); return 1


if __name__ == "__main__":
    sys.exit(main())
