"""Live end-to-end smoke test of InstantReports against a running app on :8000.

Exercises: auth (form login + Bearer), SQLite-as-data-source connection
(create/test/schema/query), report CRUD, preview (HTML+PDF), export (CSV),
and schedule creation. Uses urllib so it has no third-party deps.

Usage: python scripts/e2e_live_test.py   (app must already be running on :8000)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASS = os.environ.get("E2E_ADMIN_PASS", "Str0ng-Passw0rd!")
SAMPLE_DB = os.environ.get("E2E_SAMPLE_DB", "/tmp/sample.db")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def post_form(url: str, data: dict):
    """POST form-encoded data and return (status, set_cookie_list).

    Uses http.client (not urllib.request) on purpose: urllib's default opener
    follows the login 302 redirect and returns the *final* page's headers, which
    would hide the access_token cookie set on the redirect response.
    """
    import http.client

    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=15)
    body = urllib.parse.urlencode(data).encode()
    conn.request("POST", parsed.path, body=body,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    set_cookie = resp.getheader("Set-Cookie")  # single string (cookies joined by ", ")
    status = resp.status
    resp.read()  # drain
    conn.close()
    return status, set_cookie


def api(method: str, path: str, token: str | None, json_body=None):
    url = BASE + path
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=30)  # type: ignore[return-value]
    raw = resp.read().decode()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, raw


def api_form(method: str, path: str, token: str | None, form_fields: dict):
    """POST/GET with application/x-www-form-urlencoded body.

    Several endpoints (notably create_report) read Form(...) params rather than a
    JSON body, so the report definition must be sent as a URL-encoded JSON string
    in the form, not as an application/json payload.
    """
    url = BASE + path
    data = urllib.parse.urlencode(form_fields).encode() if form_fields else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    resp = urllib.request.urlopen(req, timeout=30)  # type: ignore[return-value]
    raw = resp.read().decode()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, raw


# 1. Auth: form login -> capture access_token cookie.
# NB: a successful login emits several Set-Cookie headers (access_token,
# current_user_id, csrf_token); http.client's .get() returns only the first,
# so scan them all for the access_token value.
try:
    status, set_cookie = post_form(BASE + "/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    import re
    m = re.search(r"access_token=([^;]+)", set_cookie or "")
    token = m.group(1) if m else None
    check("auth: form login returns redirect + access_token cookie",
          status in (301, 302, 200) and bool(token), f"status={status}")
except Exception as e:  # noqa: BLE001
    check("auth: form login", False, str(e))
    sys.exit(1)

# 2. Create SQLite data-source connection
try:
    st, body = api("POST", "/datasources/", token,
                   {"name": "E2E SQLite Sample", "connector_type": "sqlite",
                    "config": {"database": SAMPLE_DB}})
    cid = body.get("id") if isinstance(body, dict) else None
    check("datasource: create sqlite connection", st == 200 and bool(cid), f"status={st} body={body}")
except Exception as e:  # noqa: BLE001
    check("datasource: create sqlite connection", False, str(e))
    sys.exit(1)

# 3. Test connection
try:
    st, body = api("POST", f"/datasources/test/{cid}", token)
    check("datasource: test_connection succeeds", st == 200 and body.get("success") is True, f"body={body}")
except Exception as e:  # noqa: BLE001
    check("datasource: test_connection", False, str(e))

# 4. Schema
try:
    st, body = api("GET", f"/datasources/{cid}/schema", token)
    tables = body.get("schema", {}).get("tables", []) if isinstance(body, dict) else []
    check("datasource: schema lists products table", st == 200 and any(t["name"] == "products" for t in tables),
          f"tables={[t['name'] for t in tables]}")
except Exception as e:  # noqa: BLE001
    check("datasource: schema", False, str(e))

# 5. Query
try:
    st, body = api("POST", f"/datasources/{cid}/query", token,
                   {"connection_id": cid, "query": "SELECT name, price FROM products ORDER BY price DESC LIMIT 3"})
    check("datasource: query returns real rows",
          st == 200 and isinstance(body.get("preview"), list) and len(body["preview"]) == 3,
          f"row_count={body.get('row_count') if isinstance(body, dict) else None}")
except Exception as e:  # noqa: BLE001
    check("datasource: query", False, str(e))

# 6. Create report using the SQLite connection
definition = {
    "name": "E2E Product Price List",
    "layout": {"sections": [{"type": "detail", "data_source": "ds1", "elements": [
        {"type": "table", "properties": {
            "query": "SELECT name, category, price, stock FROM products ORDER BY price DESC",
            "columns": [{"field": "name", "header": "Product"}, {"field": "category", "header": "Category"},
                        {"field": "price", "header": "Price"}, {"field": "stock", "header": "Stock"}],
        }}]}]},
    "data_sources": [{"connection_id": cid}], "parameters": [],
}
# create_report reads Form(...) params (not JSON), so send name/description/
# definition/commit_message as urlencoded form fields; definition is a JSON string.
try:
    st, body = api_form("POST", "/designer/reports", token, {
        "name": "E2E Product Price List",
        "description": "E2e smoke-test report rendered against a SQLite data source",
        "definition": json.dumps(definition),
        "commit_message": "E2E live test",
    })
    rid = body.get("id") if isinstance(body, dict) else None
    check("report: create report (form-encoded) against sqlite connection",
          st == 200 and bool(rid), f"status={st} body={body}")
except Exception as e:  # noqa: BLE001
    check("report: create", False, str(e))
    sys.exit(1)

# 7. Get report editor page (returns SPA HTML, not JSON). Verify the saved name
#    persisted by checking it renders in the editor.
try:
    st, body = api("GET", f"/designer/reports/{rid}", token)
    html = body if isinstance(body, str) else json.dumps(body)
    check("report: GET editor page shows saved report name",
          st == 200 and "E2E Product Price List" in html,
          f"status={st}, has_name={'E2E Product Price List' in html}")
except Exception as e:  # noqa: BLE001
    check("report: GET", False, str(e))

# 8. Preview (HTML) -- route is /preview/{report_id}?format=html
try:
    st, body = api("GET", f"/preview/{rid}?format=html", token)
    html = body if isinstance(body, str) else json.dumps(body)
    has_data = ("Backpack" in html or "Gadget" in html) and "<table" in html.lower()
    check("preview: HTML contains product rows + table", st == 200 and has_data,
          f"status={st}, len={len(html)}, has_table={'<table' in html.lower()}")
except Exception as e:  # noqa: BLE001
    check("preview: HTML", False, str(e))

# 9. Preview (PDF) -- must preserve raw bytes; api() decodes to str and would
#    corrupt the binary stream, so do a direct byte-preserving GET here.
try:
    req = urllib.request.Request(f"{BASE}/preview/{rid}?format=pdf",
                                 headers={"Authorization": f"Bearer {token}"})
    resp = urllib.request.urlopen(req, timeout=60)
    pdf_bytes = resp.read()
    is_pdf = bytes(pdf_bytes)[:5] == b"%PDF-"
    check("preview: PDF is a valid PDF stream", is_pdf,
          f"status={resp.status}, len={len(pdf_bytes)}, head={pdf_bytes[:8]}")
except Exception as e:  # noqa: BLE001
    check("preview: PDF", False, str(e))

# 10. Export CSV via the designer temp-export path (/preview/export), which renders
#     a definition passed as a URL-encoded `definition_json` query param.
try:
    defn_json = urllib.parse.quote(json.dumps(definition), safe="")
    st, body = api("GET", f"/preview/export?format=csv&definition_json={defn_json}&name=E2EProductList", token)
    csv_text = body if isinstance(body, str) else json.dumps(body)
    check("export: CSV contains column headers", st == 200 and "Product" in csv_text and "Price" in csv_text,
          f"status={st}, head={csv_text[:80]!r}")
except Exception as e:  # noqa: BLE001
    check("export: CSV", False, str(e))

# 11. Schedule the report (fields per app/routes/admin.py create_schedule)
try:
    st, body = api("POST", "/admin/schedules", token, {
        "name": "E2E Hourly Product List", "report_id": rid,
        "cron_expression": "0 * * * *", "output_format": "csv",
        "delivery_type": "email", "recipient_emails": "e2e@example.com",
    })
    sid = body.get("id") if isinstance(body, dict) else None
    check("schedule: create schedule for report", st == 200 and bool(sid), f"status={st} body={body}")
except Exception as e:  # noqa: BLE001
    check("schedule: create", False, str(e))

# Summary
print("\n===== E2E SUMMARY =====")
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, _ in results:
    print(f"  {'PASS' if ok else 'FAIL'} {name}")
print(f"{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
