# InstantReports — Internal Master (session bridge)

**Purpose:** single at-a-glance place to pick up where we left off at the start of a session. It is
*concise and current*; deep detail lives in the two scan docs it points to.

**Last updated:** 2026-09-08 (Session 17, rounds 4-16) · **Suite:** 419 passed · **Branch:** `main`

---

## Doc map (don't hunt for detail — go to the right file)

| File | What it holds |
|------|---------------|
| **This file (`INTERNAL.md`)** | The bridge: environment, feature status, active scan, open backlog, session timeline. Read this first. |
| `SCAN_PLAN.md` | The **active scan** — agenda, prioritized execution order, and the running tally (§8). Bug/functionality/refactor findings + fix tracking. |
| `TECH_DEBT_LOG.md` | **Deep engineering log** of each fix (root cause, scope boundary, commit refs) + remaining tech-debt backlog. |

The four feature-tracker / narrative files (`CURRENT_STATUS.md`, `IMPLEMENTATION_PROGRESS.md`,
`session-summary-*.md`) were consolidated into this doc and removed to avoid drift.

---

## Environment & commands

**Test DB — live northwind PostgreSQL.** Used to validate scheduled exports / real-data integration
end-to-end (real customer rows in generated PDFs).
- Remote anchor: `10.0.1.33:5432`, db/user/pass = `northwind` (`NORTHWIND_HOST` env parametrizeable;
  `tests/integration/test_real_data_export.py` skips if unreachable).
- Local disposable mirror: `localhost:5433` via compose override / `.env.test`, seeded from `test-assets/`.

**Docker (disposable — dev stage only).** `docker compose up --build` runs the single container with
the embedded scheduler. After code changes rebuild fresh: `docker compose down -v && docker compose up --build`
(drops postgres/report-data volumes so schema/seed never drifts). App DB is **not** a source of truth.
Live schedule re-sync cadence: `SCHEDULE_SYNC_INTERVAL_SECONDS` (default 60).

**Testing — what runs where.**
- Native `.venv` (pandas + sqlalchemy only): engine, connectors registry, query-builder, non-PDF
  exporters, delivery pure-logic (email MIME / SFTP byte-stream / SMB / webhook), scheduler, authz.
- Docker only: every `app/routes/*.py` (needs fastapi/jose/passlib), `PDFExporter` (reportlab),
  `runner.py` scheduled-export + real network delivery (SFTP/SMB/SMTP).
- Lint: `.venv/bin/ruff check app/ tests/`.

**Default login:** `admin@example.com` / `admin` (auto-created on first run). Env flags: `DEBUG`,
`SEPARATE_MODE`, `USE_MOKAPI` (mokapi test server for LDAP/SMTP).

---

## Feature status — all delivery phases complete

Phases 1–10 (foundation, connectors, designer, engine/exporters, subreports, scheduler/delivery,
portal/admin, AI integration, advanced features, testing/docs) are **done**. The active work is the
**bug / functionality / refactor scan** described in `SCAN_PLAN.md` §0.

---

## Active scan — status & next

**Methodology (golden rule):** fix hard bugs + feature holes first (correctness, low risk); fold any
related refactor into the *same* commit so the fix stays testable; don't refactor purely for aesthetics.

**Prioritized execution order** (full matrix in `SCAN_PLAN.md` §1–§5):
1. Hard bugs B1–B7 — **all fixed & committed** (SFTP byte-stream, silent no-op, connector resolution,
   scheduled-export format dispatch, email MIME, chart legend, Excel param filtering, calculated-field
   code injection, SQL-parser per-operator logic).
2. Enhancements (§3) — format validation + scheduled-export audit — **done**.
3. New feature (§4) — on-demand export — **done** (`448d0ed`).
4. Cleanup (§5) — route modularization — **deferred** (bloated admin.py/preview.py; not urgent).

**Running tally (through round-3):** see `SCAN_PLAN.md` §8. Latest rounds:
- **Round-2 (Session 16):** calculated-field `eval()` code injection → AST evaluator (`a7d459f`);
  SQL-parser dropped per-condition AND/OR (`ae61c08`); duplicate-column preview drop + `filter_data`
  dead code logged, not fixed.
- **Round-3:** SMB/webhook delivery scan — **no bugs**; HMAC signing + UNC path construction verified
  correct; 14 native tests added (`97180dc`).
- **Round-4:** Core connectors (PostgreSQL/MySQL/SQL Server/ODBC) + CSV/Excel. Driver paths need real servers
  (reviewed against the `DataConnector` contract); CSV/Excel tested natively. **One bug fixed:**
  `CSVConnector.get_schema` named the field-explorer table the literal `"csv"` for every file (its config has
  no `file_name` key), inconsistent with Excel's real sheet names — now derived from the file basename. Logged
  (not fixed): every DB connector passes positional params to a raw query with no driver placeholders, so a
  future `schedule.parameters` would crash that export — unreachable today. Regression: `test_csv_excel_connectors.py` (+3). Suite **285**.
- **Round-5:** PDF exporter + scheduler. **reportlab 4.2.2 is installed natively** — PDF export logic is now
  unit-testable end-to-end (decode streams via Adobe-ASCII85 + inflate to assert rendered text); only *real
  northwind data* still needs Docker. **Bug fixed:** `_render_subreport` only handled `inline`/`drill_down`; the
  UI's **`page` ("Start on New Page")** and `detached` subreport modes fell through with no output — silently
  dropped from PDF. Now every non-inline mode renders an ASCII placeholder (parity with HTML/CSV/Excel). **Feature
  hole fixed:** `cleanup.py::send_failure_notification()` (backlog #9 "email on schedule error") had **zero call
  sites** — the scheduler only logged failures. Wired into `_execute_report`'s except handler (lazy import + its own
  try/except so a notification failure can't mask the original). Dead code noted: `scheduler/engine.py::log_audit()`.
  Regression: `test_exporters.py` (+3) + `test_scheduler.py` (+1). Suite **289**.
- **Round-6 (current):** AI client, tested live against the llama.cpp server at `http://10.0.1.37:8080/`
  (SmolLM3-3B reasoning model). **Two bugs fixed** in `app/services/ai/client.py`: `chat_completion` only read
  `message.content`, so reasoning models (empty `content`, output in `reasoning_content`) returned `''` — now falls
  back to `reasoning_content`. `AIReportGenerator`/`AILayoutAssistant` did bare `json.loads()`, which fails on the
  fenced (` ```json `) / prose-wrapped JSON local models emit — added `_extract_json_response` (strips fences, then
  extracts outermost balanced braces). Consistent with `AISQLGenerator` which already stripped sql fences. The tiny
   model occasionally returns prose for raw-JSON requests (model quality, not a bug); correctly raises there.
  Regression: `tests/unit/test_ai_client.py` (10, fake-httpx harness, no network needed in CI). Suite **299**.
- **Round-6 follow-up:** bad LLM responses now handled two ways. `chat_completion` retries transient upstream
  failures (timeouts, connection resets, 408/429/5xx) up to a configurable budget with exponential backoff;
  non-transient 4xx raise immediately. Added `classify_ai_error()` (fastapi-free, in the client module) +
  `AILLMResponseError` so routes can tell "model gave unusable output" (→ **502**) from "service flaky after
  retries" (→ **503**) vs generic 500. `app/routes/ai.py` endpoints and `nl_to_query` map failures through it,
   giving the frontend a retryable status. Regression: `tests/unit/test_ai_client.py` (+7). Suite **306**.
- **Round-7:** API keys (`app/routes/api_keys.py` + `services/api_key.py`, previously untested). **Hard bug
  fixed — cleartext secret storage:** `APIKey.key` stored the raw token; a DB read exposed every live key.
  Now stores only `sha256(key)` and returns plaintext once; `validate_api_key` looks up by hash. Migration
  `b2c3d4e5f6a7` hashes existing rows (downgrade deletes — sha256 one-way). Also hardened `validate_api_key`
  against naive datetimes (SQLite strips tzinfo → previously `TypeError` on expired keys). **Feature hole
   logged, not fixed:** `permissions` is decorative (never enforced) and API-key auth hardcodes `role="viewer"`
   regardless — product decision on whether keys should carry elevated perms. Regression: `test_api_key_service.py`
   (+6) + `test_api_keys_route.py` (+1). Suite **314**.
 - **Round-7b:** datasources (`app/routes/datasources.py`, previously untested). **Hard bug fixed — credential
   disclosure:** `GET /datasources/{id}` returned the full connection `config` (JSONB holding DB passwords/hosts)
   to *any* authenticated user, while every other config-touching endpoint requires admin/designer via
   `_require_designer`. Gated `get_connection` with `_require_designer` for parity. `list_connections` only
   returns metadata (no config) and stays open. Regression: `tests/integration/test_datasources.py` (+3: 401/
   403-no-leak/200). Suite **317**.

**Next scan candidates:** alembic migrations (low priority), remaining frontend JS deep scan.
All route/integration paths, connectors, delivery, PDF exporter, scheduler, AI client, API keys,
datasources, versions, admin, portal, auth, preview, designer, query-builder, settings, engine,
versioning service, cleanup, rendering core now scanned. Session 17 found 3 bugs + 2 holes + 1 edge case
(toast XSS, PDF Paragraph injection, cookie security, crosstab/ldap silent failures, calculated-field
column quotes). Full detail in `TECH_DEBT_LOG.md`.

**Session 17 (2026-09-08):** documentation audit + cleanup — mokapi removed entirely, separate-mode
scheduler concept dropped (single container with embedded scheduler). Engine + versioning + template scan:
**3 bugs found** — toast XSS via `innerHTML` (base.html), PDF Paragraph XML injection (pdf.py),
`current_user_id` cookie `httponly=False` (auth.py). **2 holes** — crosstab silent failure (renderer.py),
LDAP auth indistinguishable from wrong password (auth.py). **1 edge case** — calculated field column names
with single quotes break AST. Versioning/cleanup/rendering/AI client/delivery services/alembic migrations
all verified clean. All logged in `TECH_DEBT_LOG.md`.

---

## Open tech debt / deferred backlog

Full detail + rationale in `TECH_DEBT_LOG.md`:
- **Route modularization** (cleanup §5): `admin.py` 763L, `preview.py` 597L, `designer.py` 573L — split
  when a hard bug is not open.
- **Dead code:** `DataProcessor.filter_data()` defined but never called in production; `scheduler/engine.py::log_audit()`
  defined but never called (inline `AuditLog` writes in `runner.execute_report` are the real path).
- **Logged, not fixed:** query-builder "Test Query" preview drops duplicate column names (contract-
  changing; export path via pandas is unaffected).
- **Reachability-gated:** every DB connector's `execute_query` passes positional params to a raw query with
  no driver-specific placeholders (`$N`/`%s`/`@name`), so a non-empty `schedule.parameters` would crash that
  connector's export — but no route/UI populates `parameters` today. Fix is connector-specific; defer until
  parameter support lands.
- **Product decisions flagged:** CSV/Excel only render tables in `detail` sections (PDF/HTML render all);
  tabular-exporter section parity is a deliberate scope choice, not a bug.

---

## New findings (Session 17 scan — all resolved)

**Bugs (3):**
- ~~Toast XSS via `innerHTML` in `base.html:71` — server error detail executes as HTML~~ **FIXED** (`93beac3`)
- ~~PDF Paragraph XML injection in `pdf.py:149` — reportlab interprets markup from text content~~ **FIXED** (`920bc0d`)
- ~~`current_user_id` cookie `httponly=False` in `auth.py:87` — amplifies all XSS by leaking user UUID~~ **FIXED** (Session 17 round-2)

**Holes (2):**
- ~~Crosstab silent failure in `renderer.py:228` — bare except with no logging~~ **FIXED** (Session 17, already logged as `except Exception as exc:` + `logger.error`)
- ~~LDAP auth silent failure in `auth.py:141` — infrastructure error = wrong password~~ **FIXED** (Session 17 round-2: added `logger.error`)

**Edge cases (1):**
- ~~Calculated field column names with single quotes break AST in `calculated_fields.py:150`~~ **FIXED** (Session 17 round-2: escape single quotes in column names before generating `df['...']` reference)

**Verified clean:** versioning service, cleanup service, rendering core, AI client, delivery services
(SFTP/SMB/webhook), alembic migrations (all 8), chart labels (trusted definition data).

---

## Session timeline (commit refs)

**Query-builder era (2026-08-31 → 09-01):** visual SQL query builder + frontend (`config`/`generator`/
`adapter`/`optimizer`/`sql_parser`), multi-DB (pg/mysql/sqlite), optimization suggestions,
nl-to-query, template export/import; conditional-formatting wired into preview/renderer/exporters.

**Feature backlog finish (09-02 → 09-03):** lint sweep to 13 accepted errors; calculated fields (#8),
subreport config (#9), delivery UI (#10); live schedule reload via `sync_schedules()` (Session 12);
inline-toast error surfacing + standardized exception payloads (#5); runner scheduler made functional
(`40bd276`); table column autoderivation; on-demand export (`448d0ed`).

**Scan — hard bugs & fixes (Session 13–16):** B1–B7 delivery/engine/query-builder fixes; authz sweep
(query-builder API unauthenticated → `require_auth`, `902946e`); model Enum fix; preview column-header
parity (`91ef25f`); scheduled-export E2E + delivery wiring (`09776db`); subreport export parity
(`cd1acea`); report-visibility by role (`1770fc6`, `2d05143`, `68cff82`); calculated-field injection
(`a7d459f`) + SQL-parser logic (`ae61c08`); SMB/webhook delivery coverage (`97180dc`); CSV field-explorer
table-name fix (round-4); PDF subreport-mode parity + scheduled-failure notification wiring (round-5).

**Session 17 (2026-09-08):** documentation audit (mokapi dead weight, separate-mode compose gap);
engine + versioning + template scan — toast XSS (`innerHTML`), PDF Paragraph XML injection, cookie
security (`httponly=False`), crosstab/ldap silent failures, calculated-field column quote edge case.
Versioning/cleanup/rendering/chart services verified clean.

**Session 17 round-2 (2026-09-08):** fixed the three remaining unfixed findings from Session 17: cookie
`httponly=True`, LDAP auth `logger.error`, calculated-field column quote escaping. Also fixed a stale
health-check test assertion. Suite **347**.

**Session 17 round-3 (2026-09-08):** cleanup pass — removed dead code (`DataProcessor.filter_data()`,
`scheduler/engine.py::log_audit()`), fixed query-builder adapter to disambiguate duplicate column names
in result materialization (SQLite/MySQL/PostgreSQL paths all now use `_resolve_column_names`), extracted
shared auth helpers (`get_role_value`, `get_auth_source_value`, `check_role`) into `app/routes/_auth_helpers.py`
and wired them into admin/portal/designer/settings/preview/query-builder, added docker-compose test override
+ northwind seed SQL + `.env.test` for local dev against a disposable northwind database. Suite **357**.

**Session 17 round-4 (2026-09-08):** route modularization — extracted delivery config builder/redactor
into `app/services/delivery/config.py`; admin.py's create/update schedule endpoints now delegate to
`build_delivery_config()` / `redact_delivery_config()`. Regression: `tests/unit/test_delivery_config.py`
(11 cases). Suite **368**.

**Session 17 scan round-5 (2026-09-08):** query-builder optimizer + versioning service scan. Three bugs
fixed in `optimizer.py`: `group_by_no_agg` fired on empty select (now guarded); duplicate WHERE columns
produced duplicate suggestions (now deduplicated by `(code, table, column)`); `join_type.value` access
crashed on raw strings (now uses `hasattr` guard). `restore_version()` now computes `diff_summary` via
`ReportDiffEngine` so restored versions show what changed. Regression: 3 new optimizer tests + existing
versioning suite still green. Suite **371**.

**Session 17 round-6 (2026-09-08):** preview.py modularization — extracted `_build_section_html()` and
`_build_page_html()` from the ~580-line `render_report_with_data()` function. Pure HTML assembly helpers
are now independently testable. Cuts ~100 lines from the main function. Regression:
`tests/unit/test_preview_helpers.py` (5 cases). Suite **376**.

**Session 17 round-7 (2026-09-08):** enhancement — made preview row limits configurable via Settings
(`PREVIEW_TABLE_ROW_LIMIT=50`, `PREVIEW_CHART_ROW_LIMIT=10`). preview.py now uses these instead of
hardcoded values, so operators can tune the caps for large datasets without code changes. Default values
match the previous hard-coded limits. Suite **376**.

**Session 17 round-8 (2026-09-08):** preview.py modularization — extracted `_build_label_html()` pure
helper for element label rendering (handles escaping, hide_label, blank labels); removed unused
`get_role_value` top-level import. Added 4 regression tests in `test_preview_helpers.py`. Suite **380**.

**Session 17 round-9 (2026-09-08):** designer.py modularization — extracted `_build_report_export_data()`
pure helper that builds the JSON export dict for a report definition. Route handler delegates to this
helper. Added 4 regression tests in `tests/unit/test_designer_helpers.py`. Suite **384**.

**Session 17 round-10 (2026-09-08):** admin.py schedule form parsing — extracted `_parse_json_body()`,
`_validate_output_format()`, and `_parse_optional_uuid()` helpers. Both create/update_schedule endpoints
now use these, eliminating ~60 lines of duplicated parsing/validation logic. Regression:
`tests/unit/test_admin_helpers.py` (11 cases). Suite **395**.

**Session 17 round-11 (2026-09-08):** designer.py report create/update — extracted `_parse_definition_field()`
and `_build_commit_message()` helpers. Both endpoints now use these, eliminating ~30 lines of duplicated
parsing/formatting logic. Regression: `tests/unit/test_designer_report_helpers.py` (10 cases). Suite **405**.

**Session 17 round-12 (2026-09-08):** database.py cleanup — removed redundant `await session.close()` from
`get_db()` since the `async with` context manager already closes the session on exit. Suite **405**.

**Session 17 round-13 (2026-09-08):** PDF exporter modularization — extracted `_build_table_style()`,
`_compute_conditional_formatting()`, and `_apply_conditional_formatting()` helpers. The pure helpers
(`_compute_conditional_formatting()` returns directives as tuples) are independently testable without
a story list or canvas. Suite **411**.

**Session 17 round-14 (2026-09-08):** Added `generate_report_preview_summary()` helper that extracts
layout structure (section types, element counts) from a report definition. The `list_schedules` API
now includes a `preview_summary` field so the UI can display layout overviews in the schedule list.
Regression: `tests/unit/test_preview_summary.py` (4 cases). Suite **415**.

**Session 17 round-15 (2026-09-08):** Added automatic retry for scheduled-export failures. When a
schedule execution fails transiently, the scheduler retries once after 30 seconds before sending a
failure notification. Configurable via `max_retries` param (default 1). Regression:
`tests/unit/test_scheduler_retry.py` (2 cases). Suite **417**.

**Session 17 round-16 (2026-09-08):** Added recurring export history view. New `/portal/reports/{id}/history`
endpoint lists all ReportOutput rows for a specific report, scoped to user permissions. Includes a new
template (`templates/portal/report_history.html`) with format, date, size, and actions (View/Download/
Re-export). Dashboard now has a 'History' button per output. Regression: `tests/integration/test_portal_history.py`
(2 cases). Suite **419**.

---

*Keep this doc current as the scan advances — update the running tally pointer in "Active scan" and the
timeline when a new round closes.*
