"""Debug e2e report-shape + preview rendering for a SQLite-backed report."""
from __future__ import annotations
import http.client
import json
import re
import time
import urllib.request


def login():
    for _ in range(3):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=15)
            body = "email=admin@example.com&password=Str0ng-Passw0rd!".encode()
            conn.request("POST", "/auth/login", body=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse(); s = r.getheader("Set-Cookie") or ""; r.read(); conn.close()
            m = re.search(r"access_token=([^;]+)", s)
            if m:
                return m.group(1)
        except Exception as e:
            print("login retry", e); time.sleep(0.5)
    raise SystemExit("login failed")


def get_raw(path, tok):
    req = urllib.request.Request("http://127.0.0.1:8000" + path, headers={"Authorization": "Bearer " + tok})
    r = urllib.request.urlopen(req, timeout=30)
    return r.status, r.read()


tok = login()

# 1) GET the report we created earlier
rid = "d095743c-e238-4b07-9986-1cb8cc281742"
st, raw = get_raw(f"/designer/reports/{rid}", tok)
print("=== GET /designer/reports/{rid} ===")
print("status", st)
try:
    print(json.dumps(json.loads(raw), indent=2)[:1500])
except Exception:
    print(raw[:400])

# 2) Preview HTML
st, raw = get_raw(f"/preview/{rid}?format=html", tok)
print("\n=== PREVIEW HTML (status %s, %d bytes) ===" % (st, len(raw)))
txt = raw.decode("utf-8", "replace")
print(txt[:2000])
print("...")
print("contains Backpack:", "Backpack" in txt, "| contains <table:", "<table" in txt.lower())
