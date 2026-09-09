# Report Scan Phase — Agenda & Findings (Session 13+)

**Purpose:** Single source of truth for the bug / functionality / refactor scan so it can be
followed and picked up again between sessions. This doc is **analysis + prioritized backlog**, not
yet code changes. Fixes execute in the "Prioritized Execution Order" section at the bottom.

**Maintained by:** keep this updated as items move `open → fixed/deferred`.

---

## 0. Scan methodology & environment constraints

### What we CAN test natively (minimal `.venv`: pandas + sqlalchemy only)
- Engine: `app/services/engine/*` (renderer, data_processor, conditional_formatting, calculated_fields).
- Connectors registry + `resolve_data_source_connector` (`app/services/connectors/base.py`).
- Exporters: `CSVExporter`, `ExcelExporter`, `HTMLExporter` (`excel_csv_html.py`), and — since **reportlab
  4.2.2 is installed natively** now — `PDFExporter` too (streams decode via Adobe-ASCII85 + inflate, so
  rendered text is assertable in-test). Real northwind *data* for any exporter still needs Docker.
- Query builder: `config`, `generator`, `sql_parser`, `adapter`, `optimizer`, `template_io`.
- Delivery pure-logic: `email._get_mime_subtype`, `sftp`/`smb` byte-stream handling (fake modules).

### What we CANNOT test natively → test in Docker
- **Any `app/routes/*.py`** — imports FastAPI + `jose`/`passlib` at module load. (26 route tests
  error on collection here.)
- **`app/runner.py` scheduled-export path** — needs a live DB, connectors, and an exporter runtime.
- **`PDFExporter`** — needs `reportlab`.
- **Real network delivery** (SFTP/SMB/SMTP/webhook) — needs external servers.

> **Docker testing protocol:** for anything above, verify via the full stack in Docker
> (`docker compose up`) against the live `northwind` test DB (`10.0.1.33`, db/user/pass = `northwind`).
> See §7 for the recommended local test-DB setup so this doesn't require a remote host.

### Golden rule for this phase
Fix **hard bugs** and **feature holes** first (correctness, low risk). Do **refactor/cleanup** that
touches a fixed path in the *same* commit so the fix stays testable. Don't refactor purely for
aesthetics while a bug is open.

---

## 1. Hard bugs (confirmed, break user work)

| # | Location | Symptom | Impact | Fix approach | Test |
|---|----------|---------|--------|--------------|------|
| B1 | `app/services/delivery/sftp.py:44` `send_sftp` | `put_file(file_data, ...)` passed raw `bytes`; asyncssh needs a stream. Every SFTP upload raised → delivery always fails. | SFTP delivery 100% broken. | Wrap in `io.BytesIO`. | Done — `tests/unit/test_delivery_sftp.py` (fake asyncssh). |
| B2 | `app/services/delivery/sftp.py:42` `send_sftp` | `async with … put_file` block over-indented under `if key_filename:`. Password-auth (common case) skipped send, returned `True`. | Silent no-op: reports "delivered" but nothing sent. Worse than B1. | Restore `try`-body indentation. | Done — same test. |
| B3 | `app/routes/preview.py:189,309` `render_report_with_data` | Read connector type via `connection_config.get('connector_type','postgresql')`; `config` never holds it → always falls back to postgresql. | **Every non-PostgreSQL preview** (MySQL/SQL Server/REST/GraphQL/ODBC/CSV/Excel) uses wrong connector and fails. | Extract `resolve_data_source_connector()` in `connectors/base.py`; use model column. | Done — `tests/unit/test_connector_resolution.py` (5 cases). Preview path itself verified in Docker. |
| B4 | `app/runner.py:117-132` `execute_report` | Hardcodes `PDFExporter()` + `format="pdf"`. Ignores `schedule.output_format`. | **Scheduled exports always produce PDF**, even when the schedule is configured for Excel/CSV/HTML. Only rendered-export call site in the app (confirmed: no other `.export()`). | Add `get_exporter(format)` factory to `exporters/__init__.py`; dispatch on `schedule.output_format or "pdf"`. | **Docker**: create a schedule with output_format=xlsx, run once, assert output is xlsx. See §4 hole H1 (related). |

**Status:** B1–B4 all fixed & committed (`2b65601`, `a96dda4`, `2264a45`). Native factory tests (14) pass; Excel/PDF runtime output still needs Docker verification against northwind (see §6 matrix).

---

## 2. Feature holes (behavior exists in UI/DB but is incomplete or inconsistent)

| # | Location | Hole | Notes |
|---|----------|------|-------|
| H1 | `runner.py` + `exporters/` | No exporter factory; format dispatch lives nowhere. B4 is the symptom. Fixing H1 (factory) fixes B4. | See §4. |
| H2 | `admin.py` schedule form | `output_format` is stored on the schedule (line 362-363) and returned by API (523/558), but nothing downstream of storage actually reads it except via the B4 fix. Once B4 fixed, confirm Excel/CSV/HTML scheduled runs round-trip. | Verify in Docker. |
| H3 | `portal.py:129` `download_report_output` | Serves a *stored* `ReportOutput`. There is no on-demand "download as X" for an already-run report — format is locked to whatever the scheduler generated. Minor; note for enhancement §4. | — |
| H4 | Connectors without `config_fields` UI parity | All 8 connectors define `config_fields`, but preview/`test_connection` for REST/GraphQL/ODBC exercised less than pg/mysql. Untested paths = latent holes. | Docker smoke test per connector. |

---

## 3. Enhancements to the current feature set (prioritized by need/should/nice + complexity)

Complexity: **S**mall (~1 file, <150 lines), **M**edium (~1-2 files, some risk), **L**arge (multi-file, new tests).

### Should have (high value, moderate risk — do after hard bugs)
| Enhancement | Where | Complexity | Why |
|-------------|-------|-----------|-----|
| Exporter factory `get_exporter()` | `exporters/__init__.py` | S | Enables B4/H1; centralizes format→exporter + MIME. Reusable by runner + any future on-demand export. |
| Consistent `output_format` validation | `admin.py` schedule create/update + model | S | Reject/normalize unknown formats before storage so a bad value can't silently break a schedule. |
| Scheduled-export audit includes format | `runner.py execute_report` | S | Log the actual format produced (currently logs filename only). Aids debugging H2. |

### Nice to have (low risk, polish)
| Enhancement | Where | Complexity | Why |
|-------------|-------|-----------|-----|
| Reuse one `_render_element` HTML helper in preview | `preview.py` | M | Current inline if/elif chain duplicates layout logic; extracting reduces drift vs. exporters. |
| Row-count cap config | `preview.py` / settings | S | Preview hard-caps 50 rows (table) / 10 (chart); make configurable for large datasets. |

### Need to have = the hard bugs (§1). Everything else here is optional.

---

## 4. New feature suggestions (prioritized; complexity + suggested test strategy)

### Should have
| Feature | Complexity | Description | Test |
|---------|-----------|-------------|------|
| On-demand report export (any format) | M | `GET /portal/reports/{id}/export?format=pdf\|xlsx\|csv\|html` renders live + exports without creating a schedule/output row. Fills the gap vs. scheduled-only export. | Docker (needs DB + reportlab for PDF; stub or skip PDF natively). |
| Export format preview thumbnail | S | Small HTML/PDF thumbnail in schedule list so users confirm layout before running. | UI smoke test. |

### Nice to have
| Feature | Complexity | Description |
|---------|-----------|-------------|
| Recurring export history view | M | Portal page listing all `ReportOutput` for a report with re-download/re-export. |
| Scheduled-export failure retry | M | Auto-retry a failed delivery once (currently B1/B2-style bugs silently fail the schedule). |

### Need to have
- None beyond the hard bugs in §1.

---

## 5. Code cleanup / modularization (tech debt, low risk — do on isolated files)

**Bottleneck files by line count** (candidates for splitting):

| File | Lines | Problem | Suggested split |
|------|-------|---------|-----------------|
| `app/routes/admin.py` | 763 | Schedule create+update inline-build delivery config; user mgmt; audit — all in one route module. | Extract `delivery_config_builder()` helper; consider a `services/delivery/config.py`. |
| `app/routes/preview.py` | 597 | `render_report_with_data` (~490 lines) builds ALL element HTML inline (text/table/chart/crosstab/subreport/image). | Extract per-element `_render_<type>()` functions or move HTML templates to `templates/preview/*.html`. |
| `app/routes/designer.py` | 573 | Large create/update + export-definition handlers. | Extract definition-export helper (`export_report_data`) already partially present. |
| `app/routes/api/query_builder.py` | 554 | Query-builder API is large but cohesive; only split if adding new endpoints. | Monitor; don't force. |
| `app/services/query_builder/adapter.py` | 349 | Per-connector SQL adaptation. | Split per-dialect adapters into a subpackage if it grows. |
| `app/services/ai/client.py` | 280 | AI generation + SQL + insights + chat in one class. | Split into per-capability methods/subclasses. |
| `app/services/exporters/pdf.py` | 256 | reportlab table/chart/crosstab/image layout inline. | Extract `_build_flowable_<type>()` helpers. |

**Modularization that unlocks correctness (see B4/H1):** the missing exporter factory is both a
cleanup gap and the fix for B4 — do those together.

**Dead code — `DataProcessor.filter_data()` unwired.** `app/services/engine/data_processor.py:71`
implements pandas-level row filtering (`==, !=, >, >=, <, <=, contains`) but is **never called in
production** (only its own unit tests invoke it). Filters are applied at the SQL layer via the stored
element query string (`runner._fetch_element_data` → `connector.execute_query(config, query)`), so this
method is legacy. Low risk: either wire it as a pandas fallback for filters that can't be expressed in
SQL, or remove it + its tests. Recommend removal as a cleanup sweep item (not while a hard bug is open).

**Rule:** never refactor purely to reduce line count while a hard bug is open. Refactor only to
enable a fix or remove a duplicated, drift-prone path.

---

## 6. Docker / test database / deployment

### Current state
- `docker-compose.yml` + `Dockerfile` run designer (app) and runner (scheduler) modes.
- Live `northwind` Postgres at `10.0.1.33` (db/user/pass = `northwind`) is the real-data anchor for
  scheduled-export validation (per INTERNAL.md / TECH_DEBT_LOG).

### Environment note — containers & images are disposable (development stage)
- We are **purely in development**; there is no production stack. Every Docker container, image, and
  volume is a throwaway artifact. Do not persist anything that cannot be regenerated.
- After code changes, rebuild fresh rather than patching a running container:
  `docker compose down -v && docker compose up --build`. `-v` drops the postgres/report-data volumes so
  schema/seed state never drifts from a clean build.
- Verification scripts that write to the app DB (e.g. one-shot schedules, generated `ReportOutput` rows)
  should be cleaned up afterward, or just `down -v` and rebuild — the DB is not a source of truth.
- The local `northwind` mirror on `localhost:5433` (compose override / `.env.test`) is likewise
  disposable; seed it from `test-assets/` fixtures as needed.

### ENHANCEMENT — self-contained local northwind (no external server needed)
*Idea from session: stop depending on the remote `10.0.1.33` box for demos/tests.*

- Add a `docker compose` **service or compose override** that spins up Postgres preloaded with
  `northwind` sample data, exposed on `localhost:5433`, driven by an env file `.env.test`.
- `test_real_data_export.py` already targets `10.0.1.33`; parameterize the host via env
  (`NORTHWIND_HOST`) so local Docker and CI can both run it.
- **Richer demo data (nice-to-have):** add a few relationship/view seeds so reports look realistic —
  e.g. a `orders_view` / `sales_by_region` view joining customers→orders→order_details→products, and
  seed missing lookup columns the current samples reference (`product_category`, `revenue`, `profit`)
  via generated/computed columns or a view. This makes the bundled `test-assets/sample_reports/*.json`
  actually runnable against the local DB instead of fictional schemas.

**Complexity:** M — writing an init SQL + compose override is straightforward; the "realistic demo data
+ views" half is the variable part and is purely authoring work (no app code change). **Value:** high —
demos and Docker verification stop depending on an external host, and fixes the "samples reference
fictional columns" gap noted in the exporter-pipeline scan.

### Preferred deployment method
- **Docker Compose** for dev/stage (designer + runner + postgres + mokapi). Keep as primary.
- Document a single-command path: `docker compose -f docker-compose.yml -f docker-compose.test.yml up --build`.
- Runner mode: ensure `SCHEDULE_SYNC_INTERVAL_SECONDS` + `sync_schedules()` are documented for
  operators (Session 13 live-reload feature) — how to tune re-sync cadence without restart.

### Testing matrix (what runs where)
| Test | Native `.venv` | Docker |
|------|----------------|--------|
| Engine / connectors / query-builder / non-PDF exporters | ✅ | — |
| Exception handlers, scheduler reconciliation, connector resolution, SFTP byte-stream | ✅ | — |
| `PDFExporter`, full scheduled export, all routes, real delivery | ❌ (deps) | ✅ vs northwind |

---

## 7. Prioritized execution order

1. **B4 + H1** — exporter factory + scheduled-export format dispatch. ✅ committed (`2264a45`)
2. **H2** — validate `output_format` round-trips end-to-end in Docker after B4. ✅ (`chart_verify.py`)
3. **Should-have enhancements** (§3): format validation, audit log format. ✅ committed (`c0bd5d7`)
4. **B3 follow-up** — REST connector path validated natively (`test_rest_connector.py`). ✅
5. **On-demand export** (§4 should-have). ✅ committed (`448d0ed`), Docker-verified vs live northwind.
6. **Cleanup/modularization** (§5) — TRY002 bare-raises in `rest_graphql.py` → `ConnectorError` (`6b5bf0e`). ✅ All major modularization complete through Session 17 rounds 4-16 (auth helpers, delivery config, preview HTML, admin form parsing, designer helpers, PDF table styling, report preview summary). Remaining files (`query_builder.py`, `ai/client.py`) noted as "monitor; don't force".
7. **Docker/test-DB setup** (§6) — unblock the Docker testing matrix for everything above. ✅ Completed in round 3 (`docker-compose.test.yml` + `test-assets/northwind/init.sql` + `.env.test`).

---

## 8. Running tally (update as we go)
- Hard bugs fixed: B1, B2, B3, B4 (Docker-verified w/ live northwind), B5 (native tests), B6 (Docker-verified: chart PDF export crash), B7 (native tests: Excel connector param filtering without pandasql). Open: none yet — continuing scan.
- Enhancements done: format validation on schedule create/update (admin.py, 400 on bad format) + scheduled-export audit now records format/mime/size (runner.py). Both §3 should-have items done.
- **New feature done (§4 should-have): on-demand export** `GET /portal/reports/{id}/export?format=` — renders any saved report live and streams pdf/xlsx/csv/html. Shared render/fetch core extracted to `app/services/report/rendering.py` (used by both runner + route; avoids pulling delivery-stack imports into routes). Native tests: `test_rendering.py` (5). Docker-verified vs live northwind: all 4 formats return real data; 401/400/404 paths confirmed.
- B3 follow-up done: REST connector `execute_query` validated natively (test_rest_connector.py, 4 cases, real http.server) — list + `{data:[...]}` shapes, param forwarding, non-200 raises. Non-pg connector path now covered without Docker.
- Engine bug fixed: `DataProcessor` calculated fields with whitespace in `{{ }}` evaluated to null (preview/export path); unified onto shared `CalculatedFieldEvaluator`. Native regression test added (`test_engine.py`). Committed `41cd1c5`.
- Delivery bug fixed: email CSV/HTML attachments sent as `application/csv`/`application/html` (wrong maintype); clients can't open them. Derive maintype from subtype. Native tests inject fake `aiosmtplib` (`test_delivery_email.py`). Committed `cd341c4`.
- Query-builder bug fixed: multi-WHERE queries joined with only the first filter's AND/OR, ignoring per-filter logic (all connectors affected). Now each filter uses its own operator; removed dead duplicate LIKE/BETWEEN branches. Native tests (`test_query_generator.py`). Committed `3653789`.
- Versioning bug fixed: semantic diff keyed sections by `type -> single index`, so changes to the first of duplicate-type sections (extra Detail bands) were dropped from version history. Grouped indices by type in document order. Native tests (`test_versioning.py`). Committed `c6b2e6a`.
- Query-builder bug fixed: `WhereFilter.to_sql()` emitted unescaped single-quoted literals — apostrophes in data broke SQL and untrusted input enabled injection. All literals now escaped via shared `_quote()` (doubles `'`). Native tests (`test_query_generator.py`). Committed `ee33db0`.
- Latent/low-reachability: Postgres `execute_query` passes positional params to `asyncpg.fetch(query, *params)` but the generator inlines values with no `$N` placeholders — a non-empty `schedule.parameters` would crash Postgres exports. `schedule.parameters` is never populated by any route/UI today, so not currently reachable; log for when parameter support is added.
- Minor finding (cleanup §5): RESOLVED (`6b5bf0e`) — 3 bare `raise Exception` in rest_graphql.py now raise `ConnectorError`. Remaining cleanup = modularization of bloated routes (admin.py 763L, preview.py 597L); deferred, not urgent.
- Holes: H1 resolved by B4's factory; H2/H3/H4 tracked above; **exporter element-coverage hole half-closed** — HTML now inlines charts (Excel/CSV intentionally tabular, documented).
- Cleanup done: TRY002 bare-raises → ConnectorError. Docker/test-DB: not set up.

### Route/integration layer — now runnable locally (Session 15)
Installing the missing core deps (`fastapi`, `jose`, `passlib`, `bcrypt<4`, `jinja2`, `arrow`,
`python-multipart`) unblocked the route tests that §0 marked "Docker-only". Full suite: **241 passed / 0
failures / 0 errors** (was 191 passed with 26 collection errors). This surfaced two real findings:
- **HARD BUG (fixed) — template export route shadowed (`app/routes/api/query_builder.py`).** `GET
  /templates/{template_id}` was registered *before* `GET /templates/export`, so Starlette matched
  `/templates/export` to the param route with `template_id="export"` → `uuid.UUID("export")` raised 400.
  The export endpoint was completely unreachable. Fixed by registering `/templates/export` before the
  parameterized route. Regression: `tests/integration/test_template_import.py` (9 tests) now pass.
- **Model bug (fixed) — `UserRole`/`AuthSource` imported SQLAlchemy's `Enum` (a column type) instead of
  Python's `enum.Enum` (`app/models/user.py:6`).** Members were plain strings with no `.value`; the app
  survived only via `hasattr(x, 'value')` guards scattered across routes. Made them real `str, Enum`
  members (storage on `String(50)` columns still writes the bare value — verified). Test-infra bugs that
  were masked while route tests couldn't collect were also corrected: conftest `__import__("app.auth")`
  returns the `app` package (no `hash_password`) → switched to a proper import; login/logout redirect is
  standard **302** (test expected non-standard 307); `test_edit_report`/`test_list_versions` read the id
  from the 303 `Location` header instead of `.json()` on an empty body; `test_restore_version` created a
  real `Report` (was passing a user id as `report_id`).
- **App inconsistency noted (not changed):** `auth.py` redirects use 302 while `portal.py`/`designer.py`
  use 307. 302 is HTTP-correct for the POST-login redirect; left as-is. Flagged for a deliberate
  consistency decision later.

### Scheduled-export E2E + preview/export rendering drift (Session 16)
- **Full scheduled-export path verified end-to-end against live northwind** via `runner.execute_report`
  (not just the shared on-demand core): created real one-shot Schedule rows (csv + xlsx) in the app DB,
  invoked `execute_report` directly, and asserted the produced `ReportOutput` bytes contain real
  northwind data (`USA,122,263566.98`, Germany; XLSX `Country/Orders/Revenue` + USA row). Confirms the
  whole chain `schedule -> _fetch_element_data -> DataProcessor -> ReportRenderer -> export_report ->
  ReportOutput` works with format dispatch on csv/xlsx. (On-demand route via :8080 also re-verified.)
- **RENDERING DRIFT BUG (fixed) — preview table headers ignored configured column labels.**
  `render_report_with_data` (`app/routes/preview.py`) rendered table `<th>` cells from raw `df.columns`
  (SQL result names: `country`, `order_count`, `revenue`) while exporters use the element's configured
  `columns[].header` (`Country`, `Orders`, `Revenue`). Designers saw different column names in preview vs.
  export. Fixed preview to honor configured headers (display label) while keeping data/conditional-
  formatting lookups on the field; falls back to raw df columns for query-only tables with no configured
  columns. Mirrors the exporter's field/header split. Regression: `tests/unit/test_preview_rendering.py`
  (3 cases). Full suite **244 passed / 0 failures** (was 241).
- **Environment doc added (§6):** containers/images/volumes are disposable dev artifacts —
  `docker compose down -v && up --build` after code changes; app DB is not a source of truth. We are
  purely in development stage.
- **DELIVERY BUG (fixed) — scheduled reports generated but never delivered.** `scheduler/engine.py
  _execute_report` discarded `execute_report()`'s return value; `runner.deliver_report()` had no call
  site, so every schedule with a delivery config (email/SFTP/SMB/webhook) wrote a `ReportOutput` to the
  portal but never sent. Fixed via new `_deliver_scheduled(output, schedule_id, db)` helper that loads the
  schedule's active deliveries + their `DeliveryRecipients` (FK order matters in Postgres: Schedule→Delivery→Recipient; ReportOutput/AuditLog also
  reference these) and delegates to `runner.deliver_report`. Tests: `test_scheduler.py`
  (3 new). Full suite 247 passed. E2E vs live northwind: CSV scheduled export reached a live HTTP
  endpoint with correct metadata.
- **Connector scan (Session 16): REST auth + GraphQL crash.** `rest_graphql.py` read REST auth from a
  nested `config["auth"]` object that the form never populates (flat `auth_type`/`auth_token` keys) → bearer
 /basic auth silently dropped. GraphQL `_make_request` did `**config["headers"]` where `headers` is a JSON
  textarea **string** → `TypeError: 'str' object is not a mapping` on every request (GraphQL fully broken).
   Fixed with `_coerce_json_field()` helper; 4 regression tests in `test_rest_connector.py`. Suite **251
   passed**. ODBC + CSV/Excel scanned, structurally sound.
- **Chart scan (Session 16):** `engine/chart.py` called `ax.legend()` unconditionally on single-series
  charts → matplotlib `UserWarning: No artists with labels found to put in legend` on every chart + a blank
  legend box rendered in the corner. Removed the call; all four chart types still emit valid PNGs and the
  warning is gone from the suite summary.
- **Authz sweep (Session 16):** static scan of every route for unguarded auth. Found the query-builder API
  used a `get_current_user_simple()` stub returning `None` unconditionally → all 16 routes (`/test`, `/schema`,
  template save/import, AI `nl-to-query`, …) were effectively **unauthenticated**, including raw-SQL execution
  against real connections. Replaced with `require_auth()` (decodes JWT/cookie via `get_current_user_optional`,
  raises 401). Also hardened `preview_websocket()` (accepted the WS before checking `current_user`; echo-only,
   no data exposure, no frontend consumer). Confirmed every other route enforces auth (via `if not current_user`
   or `_require_designer`); only `login`/`logout` are correctly public. Full suite **252 passed**.

### Session 16 — round 2 (engine / query-builder)
- **CALCULATED-FIELD CODE INJECTION (fixed, CWE-94 High).** `calculated_fields.py::evaluate()` ran user
  expressions through Python `eval()` with only an empty `__builtins__` (trivially escapable via
  `().__class__.__bases__[0].__subclasses__()`) and never called `validate_expression()`. Reached via the
  Fields-tab Test endpoint AND the preview/scheduled-export path (stored report `calculated_fields`).
  Replaced with an AST-based evaluator (`_SafeExpressionEvaluator`) allowing only `df['col']` lookups,
  arithmetic/comparison ops, and a pure-function whitelist; everything else → null. Tests:
  `test_engine.py` (parametrized injection cases). Suite **267**.
- **SQL PARSER DROP-PER-OPERATOR LOGIC (fixed).** `sql_parser.py::parse_sql_to_config` tagged every WHERE
  condition with the *last-seen* AND/OR, so `A AND B OR C` round-tripped as `A OR B OR C`, silently
  altering semantics for AI/pasted SQL. Fixed by pairing each condition with its preceding operator; removed
  a dead shadowed `_OPERATOR_PATTERN`. Test: `test_sql_parser.py::test_where_preserves_per_operator_logic`.
  Suite **268**.
- **Logged, not fixed:** query-builder "Test Query" preview drops duplicate column names via
  `dict(zip(names, row))` (preview-only; export uses pandas which preserves dups — contract-changing fix
  deferred). Dead code: `data_processor.filter_data()` has no callers.
- **Verified clean:** file ops (image upload = UUID filenames; exports use `tempfile.mkstemp`), AI routes
  (auth-guarded; generate-sql returns SQL for review, not auto-executed; schema is structure-only → no
  prompt-injection→SQLi chain). No SQL string-construction injection patterns found.
- **DELIVERY SCAN (SMB + webhook, Session 16 round-3):** `smb_webhook.py` had **zero** test coverage —
  the highest-risk delivery path because its broad `except` turns any failure into a silent `False`
  (delivery "succeeds" on paper but nothing is sent). Verified natively (httpx installed; smbprotocol/
  smbclient faked via `sys.modules`, same pattern as the SFTP tests): HMAC signing correct
  (`X-Webhook-Signature: t=<unix>,v1=<sha256(secret, body)>`), independently recomputed and matched; JSON
  body + `Content-Type` set; `secret=None` omits signature; custom headers merged; HTTP ≥400 / connection
   errors → `False`; `test_webhook_connection` URL guard works. SMB UNC path construction confirmed correct
   (`\\server\share/<remote_path>/<file>`, forward-slash remote form matches smbprotocol convention);
   `register_session` called with flat creds; missing-module path → `False`. No bugs found. Regression:
   `tests/unit/test_delivery_smb_webhook.py` (14). Suite **282 passed**.
- **CONNECTOR SCAN (core DB + CSV/Excel, Session 16 round-4):** scanned PostgreSQL / MySQL / SQL Server /
  ODBC / CSV / Excel. Driver paths (asyncpg/asyncmy/pymssql/aioodbc) need real servers → verified by code
  review against the shared `DataConnector` contract; CSV/Excel run on pandas so tested natively. Grouping
  algorithm in every `get_schema` is correct (ordered-by-table, consecutive-run grouping). Placeholder style
  per driver is right (pg `$N`, mysql `%s`, sqlserver `@schema`, odbc positional). **One bug fixed:**
  `CSVConnector.get_schema` named the table `config.get("file_name", "csv")`, but `file_name` is not in CSV's
  `config_fields`, so the field explorer showed a literal `"csv"` for *every* CSV instead of the real filename
  (Excel correctly uses real sheet names). Now derives the name from the file basename (`os.path.splitext`).
   Also logged (not fixed, consistent with existing findings): the parameter path in every DB connector passes
   positional params to a raw query with no `$N`/`%s` placeholders — a non-empty `schedule.parameters` would
   crash that connector's export; unreachable today since nothing populates `parameters`. Regression:
   `tests/unit/test_csv_excel_connectors.py` (+3, incl. CSV-filename + multi-sheet-Excel schema). Suite **285**.
- **PDF EXPORTER SCAN (Session 16 round-5):** reportlab 4.2.2 is installed natively, so PDF export is now
  unit-testable end-to-end (`export()` → valid `%PDF` bytes; streams decoded via Adobe-ASCII85 + inflate to
  assert rendered text). **Bug fixed — non-inline subreports were silently dropped from PDF.** `_render_subreport`
  only special-cased `inline` (embed) and `drill_down` (placeholder); the UI offers a third mode, **`page`
  ("Start on New Page")** and also `detached`, both of which fell through with *no* output — a "Start on New
  Page" subreport vanished from the exported PDF with no indication. The HTML/CSV/Excel exporters already emit
  a placeholder for any non-inline mode (cd1acea), so PDF was inconsistent. Now every non-inline mode
  (`drill_down`/`page`/`detached`) renders an ASCII placeholder paragraph `Sub-report (<mode>) - content not
  embedded in <fmt> export` (hyphen, not em dash — stays within Helvetica's charset). Regression:
  `tests/unit/test_exporters.py::TestSubreportExport` (+3: inline embeds nested content; page/detached/drill_down
  each get the placeholder; end-to-end `page`-mode export yields a valid `%PDF`). Verified the placeholder text
  is genuinely in the rendered PDF bytes. Suite **288**.
- **SCHEDULER SCAN (Session 16 round-5):** reviewed `scheduler/engine.py`, `runner.py` (`execute_report`,
  `run_scheduler`, `deliver_report`, `cleanup_old_reports`), `services/cleanup.py`. Reconciliation, one-shot
  skip, stale-job removal (UUID-shaped only; `cleanup_` protected), delivery wiring, and owner attribution are
  all correct and covered. **Feature hole fixed — schedule-failure notifications were never sent.**
  `cleanup.py::send_failure_notification()` (the backlog #9 "email on schedule error" feature) had **zero call
  sites**: `_execute_report`'s except handler only logged, so a failed scheduled report notified no one. Wired
  it into the handler, importing lazily (keeps this module's load path decoupled from the aiosmtplib-dependent
  delivery stack) and wrapping in its own try/except so a notification failure can't mask the original error.
  Regression: `tests/unit/test_scheduler.py::test_execute_report_sends_failure_notification_on_error` (monkeypatches
  `execute_report` to raise; asserts `send_email` is called with the schedule name + error). Suite **289**.
   **Dead code noted:** `scheduler/engine.py::log_audit()` is defined but never called — inline `AuditLog` writes
   in `runner.execute_report` are the actual path. Leave for a cleanup pass.
- **AI CLIENT SCAN (Session 16 round-6):** tested against the live llama.cpp server at
  `http://10.0.1.37:8080/` (model `/opt/llama.cpp/Models/SmolLM3-3B-128K-Q4_K_M.gguf`, a ~3B reasoning model;
  slower + lower accuracy than prod, but valid for smoke-testing the OpenAI-compatible path). `AISQLGenerator`
  works natively (`generate_sql` → `SELECT name FROM customers;`). Two failure modes found & fixed in
  `app/services/ai/client.py`:
  - `chat_completion` only read `message.content`; reasoning models leave `content` empty and populate
    `reasoning_content`, so it returned `''`. Now falls back to `reasoning_content` when `content` is blank —
    covers both instruct and reasoning endpoints (prefers `content` when both present).
  - `AIReportGenerator` / `AILayoutAssistant` did bare `json.loads()`, which fails on fenced (` ```json `) or
    prose-wrapped JSON that local models commonly emit. Added `_extract_json_response`: strips markdown fences,
    then extracts the outermost balanced `{...}`/`[...]` block. Makes the JSON generators consistent with
    `AISQLGenerator` (which already stripped sql fences).
  Regression: `tests/unit/test_ai_client.py` (10 incl. a fake-httpx harness so no network is needed in CI).
  Suite **299 passed**; ruff clean. Note: the tiny model also returns prose instead of JSON when asked for raw
  JSON — that's model quality, not a client bug; `_extract_json_response` correctly raises `JSONDecodeError`
  (→ the generators' `ValueError`) in that case.
- **AI CLIENT RETRY + ERROR CLASSIFICATION (round-6 follow-up):** bad LLM responses now handled two ways.
  `chat_completion` retries transient upstream failures (timeouts, connection resets, 408/429/5xx) up to a
  configurable `retries` count with exponential backoff (`retry_backoff * 2**attempt`); non-transient 4xx raise
  immediately. Added `classify_ai_error()` in the (fastapi-free) client module so every route maps failures
  consistently: `AILLMResponseError` (model ran but returned unusable output) → **502**, transient transport
  errors after the budget is exhausted → **503**, other → **500**. Report/layout generators now raise
  `AILLMResponseError` instead of bare `ValueError` so the distinction survives. `app/routes/ai.py` endpoints and
  the `nl-to-query` route route failures through `classify_ai_error`, giving the frontend a retryable status
  (502/503) instead of a generic 500. Regression: `tests/unit/test_ai_client.py` (+7: retry-then-succeed,
  no-retry-on-4xx, budget exhaustion, connection-error retry, all three classify branches; fake httpx with a
   proper exception hierarchy, backoff=0). Suite **306 passed**; ruff clean.
- **API KEYS SCAN (Session 16 round-7):** `app/routes/api_keys.py` + `services/api_key.py` had **no** tests.
  **Hard bug fixed — API keys stored in cleartext.** `APIKey.key` persisted the raw `ir_<token>` token; a DB
  read exposed every live key. `generate_api_key` now stores only `sha256(key)` and returns the plaintext to
  the caller once; `validate_api_key` looks up by hash. Migration `b2c3d4e5f6a7` hashes existing rows in place
  (verified against a fresh SQLite `api_keys` table: plaintext→digest, no leak; downgrade deletes rows since
  sha256 is one-way). Alembic graph intact — chains onto `a1b2c3d4e5f6`, heads are `b2c3d4e5f6a7` + the
  pre-existing separate `c7d8e9f0a1b2`. Also hardened `validate_api_key` against naive datetimes from drivers
  that strip tzinfo (SQLite) — previously raised `TypeError` on expired keys instead of rejecting them.
  **Feature hole (logged, not fixed):** `permissions` is decorative — `create_key` accepts a permissions list
  but no route enforces it, and `get_current_user_from_api_key` hardcodes `role="viewer"` regardless. Whether
  keys should carry admin/execute perms is a product decision; flagged rather than reimplemented. Regression:
   `tests/unit/test_api_key_service.py` (+6) + `test_api_keys_route.py` (+1, dependency-override proving
   plaintext returned / hash stored). Suite **314 passed**; ruff clean.
 - **DATASOURCES SCAN (Session 16 round-7b):** `app/routes/datasources.py` had **no** dedicated tests.
   **Hard bug fixed — credential disclosure via un-gated single-connection GET.** `GET /datasources/{id}`
   returned the full connection `config` (JSONB blob holding DB passwords, hosts, etc.) to *any* authenticated
   user, while every other config-touching endpoint (`create`/`update`/`delete`/`test`/`schema`/`query`/
   `calculate`) requires admin/designer via `_require_designer`. A plain authenticated user could read another
   connection's secrets. Gated `get_connection` with `_require_designer` for parity (designers already have full
   create/update/delete control, so no additional exposure). `list_connections` only returns metadata (no
    config) and stays open to any authed user. Regression: `tests/integration/test_datasources.py` (+3: 401
    unauth, 403 viewer with no config leaked, 200 designer). Verified the test fails without the fix (returns
    200). Suite **317 passed**; ruff clean.
 - **VERSIONS SCAN (Session 16 round-7c):** `app/routes/versions.py` (`/designer/reports/*`) had **no**
   dedicated tests. **Hard bug fixed — version endpoints ignored report ownership.** All nine endpoints gated on
   authentication (and mutations on admin/designer role) but none checked whether the user could touch that
   report, so any logged-in user could read/mutate another team's version history and past `definition` JSON —
   contradicting the `designer.list_reports` model (admins: any report; others: only reports they created). Added
   `_require_report_access()` (404 missing / 403 non-owner-non-admin) and wired it into every endpoint; comment
   deletion stays owner-scoped in the service. Regression: `tests/integration/test_versions_auth.py` (+5:
   non-owner designer 403 on read versions/detail + mutate (restore/tag), owner reads own (200), admin reads any
    (200)). Confirmed the 403 tests fail without the fix. Suite **322 passed**; ruff clean.
 - **ADMIN SCAN (Session 16 round-7d):** `app/routes/admin.py` (`/admin/*`, ~787L — user + schedule + audit mgmt)
   had no dedicated tests. **Hard bug fixed — schedule read endpoints leaked delivery secrets.** `list_schedules`
   and `get_schedule_api` serialized `delivery_config` verbatim; for SFTP/SMB that holds the plaintext `password`,
   for webhook the `secret`. Both are gated only to admin/designer, so any such user could enumerate every
   schedule and read those credentials — a credential-disclosure parallel to round-7b. Added `redact_delivery_config()`
   (recursively masks `password`/`secret`, leaves other fields) applied to both read endpoints; create/update still
   accept+store secrets, only responses are redacted. Email configs carry no secret so pass through unchanged.
    Regression: `tests/integration/test_schedule_secret_leak.py` (+1). Suite **323 passed**; ruff clean.
 - **PORTAL SCAN (Session 16 round-7e):** `app/routes/portal.py` (end-user report portal). `portal_index`
   correctly scopes non-admins to "outputs they generated or reports they own schedules for" (mirrors
   `designer.list_reports`), but the per-report endpoints only required authentication. **Hard bug fixed — IDOR
   on per-report portal endpoints.** `view_report_output`, `download_report_output`, `export_report` and
   `get_report_parameters` took `{report_id}`/`{output_id}` path params and returned 404 only when the object did
   not exist — no ownership check — so any logged-in user could view/download/export another user's output and read
   its parameters by directly requesting the IDs. `export_report` renders a report against **live data** and streams
   bytes, making it the worst. Added `_can_access_report()` (admin OR owns a schedule for the report) and
   `_can_access_output()` (admin OR generated_by self OR owns a schedule) mirroring `portal_index`, wired into all
    four endpoints (403 on failure). Regression: `tests/integration/test_portal.py` (+2). Suite **325 passed**; ruff
    clean.
 - **AUTH SCAN (Session 16 round-8):** `app/config.py` `Settings.SECRET_KEY` defaulted to `"change-me-in-production"`
   with no guard; `app/auth.py` signs JWTs with it (HS256) → anyone can forge an admin token (full auth bypass).
   Proved: a token created with the default secret decodes via `decode_access_token` to `sub=admin@example.com`. Added
   a pydantic `@field_validator("SECRET_KEY")` that raises at startup on the placeholder (fail fast). `conftest.py`
   sets a real test key via `os.environ.setdefault(...)` before any `app.*` import so `Settings()` validates.
   **Verified clean:** algo restriction (no confusion), jose auto-rejects expired tokens, `authenticate_user` checks
   `auth_source==LOCAL` + bcrypt, LDAP re-binds as the user. Regression: `tests/unit/test_auth.py` (+2). Suite
   **327 passed**; ruff clean.
 - **PREVIEW SCAN (Session 16 round-8b):** `app/routes/preview.py`. `preview_report` correctly scopes
   (admin/designer any report; other roles only their own), but `GET /preview/temp` — the designer Preview button —
   only required authentication. It renders a client-supplied definition and executes each table element's query
   against an application data-source connection (falling back to the first PostgreSQL connection), so any logged-in
   user (incl. viewers) could run arbitrary SQL against configured DBs. Fixed with the same admin/designer role gate
   as `preview_report`. `resolve_data_source_connector` pulls config from the stored `DataConnection` (client can't
   inject creds). Lower-priority follow-up: even designers can hit any connection via fallback/enumerated ID — a
   scoped connection binding would be cleaner than a role gate alone. Regression: `test_preview_auth.py` (+2). Suite
   **330 passed**; ruff clean.
 - **DESIGNER SCAN (Session 16 round-8c):** `app/routes/designer.py`. `list_reports` correctly scopes non-admins to
   reports they created ("designers only see reports they created ... never leak other teams' reports"), but
   `designer_index` (GET `/designer/`, the landing page) listed **all** reports to any admin/designer with no creator
   scoping — contradicting `list_reports` and exposing every report's name/ID to any designer. Fixed by aligning
   `designer_index` with `list_reports` (admins all; other roles `created_by == current_user.id`). Regression:
   `test_api.py::test_designer_index_scopes_to_own_reports` (+1). Verified it fails without the fix. Suite **328
   passed**; ruff clean.
 - **QUERY-BUILDER TEMPLATE SCAN (Session 16 round-8d):** `app/routes/api/query_builder.py`. Template list/get/export/
   delete operated on any template by ID with no ownership check, so any authenticated user could read other users'
   saved query templates (query_config holds raw SQL) or delete them. Templates carry a created_by but were never
   scoped. Added `_require_template_access()` (admin oversees all; other roles only their own) wired into list/get/
   export/delete; import now sets created_by=importer (was ownerless). Existing import/export tests still pass (run as
   admin). Regression: `test_query_builder_auth.py` (+4). Suite **334 passed**; ruff clean.
 - **PREVIEW XSS (Session 16 round-8f):** `preview.py::render_report_with_data` interpolated table cell values, text
   content, section/element labels and chart labels into preview HTML with raw f-strings, no escaping — a report pulling
   untrusted rows could inject `<script>` into the designer's preview (stored XSS). Escaped all data-derived
   interpolations with `html.escape()` (quote=True on cell/text); numeric format specs unaffected; `render()` dict path
       unchanged. Regression: `test_preview_rendering.py` (+1). Suite **344 passed**; ruff clean.
 - **EXPORT XSS (Session 16 round-8g):** `excel_csv_html.py::HTMLExporter._render_table_html` appended data-derived
    cell values into `<td>` with raw f-strings, no escaping — the export counterpart of round-8f. Downloaded `.html`
    reports with untrusted rows would execute markup when opened (stored XSS). Escaped cell values + data-derived headers
    with `html.escape(str(...), quote=True)`; trusted conditional-formatting style attrs left intact. Regression:
    `test_exporters.py` (+1). Suite **345 passed**; ruff clean.
 - **LDAP INJECTION (Session 16 round-8h):** `app/auth.py::authenticate_ldap_user` embedded the login email into an
   LDAP search filter with no escaping (CWE-546); an email like `*)(uid=)(*` could restructure the filter for user
   enumeration. Added `escape_ldap_filter()` (RFC 5012 `\ * ( )` escaping) applied before the filter is built. Bounded
   by the follow-up password bind. Regression: `test_auth.py::TestLdapInjectionGuard` (+2). Suite **345 passed**; ruff
   clean.
  - **QUERY-BUILDER SQLI (Session 16 round-8e):** `config.py` interpolates table/column/alias/join/direction/group_by
    identifiers *unquoted* into generated SQL, and `/test` executes that SQL against a live connection — so a crafted
    QueryConfig could inject SQL (round-8 only quoted WHERE values). Fixed with `_validate_identifier()` (dotted alnum/
    underscore; empty allowed for parser back-compat) on SelectColumn/JoinConfig/WhereFilter/OrderByField + direction
    restricted to ASC/DESC, plus from_tables/group_by validation — rejected at the pydantic boundary so /test,/validate,
    /generate-sql,/save all block it. Regression: `test_query_builder_sqli.py` (+9). Suite **343 passed**; ruff clean.

### Session 17 — documentation audit + cleanup (2026-09-08)
- **Mokapi removed.** All references dropped from `docker-compose.yml`, `.env.example`, `settings.html`,
  `app/config.py` (`USE_MOKAPI`), and `test-assets/mokapi/`. Never configured; will be handled separately.
- **Separate-mode scheduler dropped.** `SEPARATE_MODE` removed from `Settings`. README updated — the app
  runs as a single container in designer mode with the embedded scheduler. No dedicated runner container.

### Session 17 round-2 — remaining bugs from Session 17 scan (2026-09-08)
- **COOKIE SECURITY (fixed):** `auth.py` sets `current_user_id` cookie with `httponly=False`; both cookie
  writes now use `httponly=True`. Prevents JS read of the authenticated user's UUID, reducing XSS impact.
- **LDAP AUTH SILENT FAILURE (fixed):** `authenticate_ldap_user` wrapped its body in `except Exception: return None`
  — an LDAP server timeout was indistinguishable from a wrong password. Added `logger.error()` with the email
  and exception message before returning None. Regression: `tests/unit/test_auth.py::TestLdapAuthLogging` (+1).
- **CALCULATED FIELD COLUMN QUOTE INJECTION (fixed):** `_process_expression` built `df['O'Brien']` verbatim for
  column names containing single quotes — the AST parser rejected it as a syntax error, returning all-None. Now
  escapes single quotes via `col.replace("'", r"\'")` before generating the replacement string. Regression:
  `tests/unit/test_engine.py::test_process_expression_escapes_single_quotes_in_column_names` (+1).
- **STALE TEST (fixed):** `test_health_check` asserted `"mode" in data` but the `/health` endpoint was simplified
  to `{"status": "ok"}` when separate-mode was dropped. Removed the stale assertion.

### Session 17 round-3 — cleanup pass (2026-09-08)
- **Dead code removed:** `DataProcessor.filter_data()` (no callers in production) + its 3 tests;
  `scheduler/engine.py::log_audit()` (never called, inline `AuditLog` writes are the real path).
- **Duplicate column fix:** query-builder adapter `execute_query` now disambiguates duplicate column names
  via `_resolve_column_names()` across all three backends (PostgreSQL/SQLite/MySQL). Regression:
  `tests/unit/test_query_adapter.py::test_execute_sqlite_duplicate_column_names`.
- **Auth helpers extracted:** `get_role_value`, `get_auth_source_value`, `check_role` moved to
  `app/routes/_auth_helpers.py`; all consuming routes (`admin`, `designer`, `portal`, `settings`,
  `preview`, `api/query_builder`) updated. Eliminates duplicated Enum-vs-string role extraction.
  Regression: `tests/unit/test_auth_helpers.py` (12 cases).
- **Local northwind dev setup:** `docker-compose.test.yml` override + `test-assets/northwind/init.sql`
  (customers, employees, products, orders, order_details, views) + `.env.test`. Integration test now
  reads `NORTHWIND_*` env vars with fallback to remote anchor.

### Session 17 round-4 — delivery config extraction (2026-09-08)
- **Delivery config extracted:** `build_delivery_config()` and `redact_delivery_config()` moved from
  inline blocks in `admin.py` into `app/services/delivery/config.py`. Both schedule create and update
  endpoints now delegate to the shared builder; the redactor is imported for list/get responses.
  Eliminates ~50 lines of duplicated branching logic. Regression: `tests/unit/test_delivery_config.py`
  (11 cases covering email/sftp/smb/webhook types, nested redaction, edge cases). Suite **368**.

### Session 17 scan round-5 — optimizer + versioning (2026-09-08)
- **Optimizer BUG-1 fixed:** `group_by_no_agg` check now requires `config.select` to be non-empty before
  checking for aggregates, so `SELECT * ... GROUP BY` is no longer incorrectly flagged.
- **Optimizer BUG-2 fixed:** `analyze_query()` now deduplicates suggestions by `(code, table, column)` so
  repeated WHERE filters on the same column emit a single suggestion.
- **Optimizer BUG-5 fixed:** `join_type` comparison now uses `hasattr(value, "value")` guard so plain
  string join types (bypassing Pydantic validation) no longer crash with `AttributeError`.
- **Versioning restore diff_summary:** `restore_version()` now computes `diff_summary` via `ReportDiffEngine`
  by diffing the current definition against the restored one, so restored versions show what changed.
   Regression: 3 new optimizer tests in `tests/unit/test_query_optimizer.py`. Suite **371**.

### Session 17 round-6 — preview.py HTML builder extraction (2026-09-08)
- **Preview modularization:** extracted `_build_section_html()` and `_build_page_html()` from the ~580-line
`render_report_with_data()`. Pure HTML assembly helpers are now independently testable. Cuts ~100 lines
from the main function. Regression: `tests/unit/test_preview_helpers.py` (5 cases). Suite **376**.

### Session 17 round-7 — configurable preview row limits (2026-09-08)
- **Config enhancement:** added `PREVIEW_TABLE_ROW_LIMIT` (default 50) and `PREVIEW_CHART_ROW_LIMIT`
(default 10) to Settings. preview.py now uses these instead of hardcoded values, so operators can tune
the caps for large datasets without code changes. Default values match the previous hard-coded limits.
Suite **376**.

### Session 17 round-8 — preview.py label helper extraction (2026-09-08)
- **Preview modularization:** extracted `_build_label_html(elem_label, hide_label)` pure helper that
builds the element label div with proper HTML escaping. Handles blank labels and hide_label=True.
Removed unused top-level `get_role_value` import. Regression: 4 new tests in `test_preview_helpers.py`.
Suite **380**.

### Session 17 round-9 — designer.py export helper extraction (2026-09-08)
- **Designer modularization:** extracted `_build_report_export_data(report, current_user)` pure helper
that builds the JSON export dict for a report definition. Route handler delegates to this helper.
Regression: 4 new tests in `tests/unit/test_designer_helpers.py`. Suite **384**.

### Session 17 round-10 — admin.py schedule form parsing helpers (2026-09-08)
- **Admin modularization:** extracted `_parse_json_body()`, `_validate_output_format()`, and
`_parse_optional_uuid()` pure helpers. Both create/update_schedule endpoints now use these, eliminating
~60 lines of duplicated parsing/validation logic. Regression: `tests/unit/test_admin_helpers.py` (11
cases). Suite **395**.

### Session 17 round-11 — designer.py report create/update helpers (2026-09-08)
- **Designer modularization:** extracted `_parse_definition_field()` and `_build_commit_message()` pure
helpers. Both create_report and update_report endpoints now use these, eliminating ~30 lines of
duplicated parsing/formatting logic. Regression: `tests/unit/test_designer_report_helpers.py` (10
cases). Suite **405**.

### Session 17 round-12 — database.py session close cleanup (2026-09-08)
- **Database cleanup:** removed redundant `await session.close()` from `get_db()` in `app/database.py`.
The `async with` context manager already closes the session on exit, so the explicit close in the
finally block was unnecessary. Suite **405**.

### Session 17 round-13 — PDF exporter table styling extraction (2026-09-08)
- **PDF modularization:** extracted `_build_table_style()`, `_compute_conditional_formatting()`, and
`_apply_conditional_formatting()` helpers. The pure helper `_compute_conditional_formatting()` returns
directives as tuples, testable without a story list or canvas. Regression: `tests/unit/test_pdf_exporter_helpers.py`
(6 cases). Suite **411**.

### Session 17 round-14 — report preview summary for schedule list (2026-09-08)
- **New feature:** added `generate_report_preview_summary()` helper that extracts layout structure
(section types, element counts) from a report definition. The `list_schedules` API now includes a
`preview_summary` field so the UI can display layout overviews in the schedule list. Regression:
`tests/unit/test_preview_summary.py` (4 cases). Suite **415**.

### Session 17 round-15 — scheduled-export failure retry (2026-09-08)
- **Reliability improvement:** added automatic retry for scheduled-export failures. When a schedule
execution fails transiently, the scheduler retries once after 30 seconds before sending a failure
notification. Configurable via `max_retries` param (default 1). Prevents transient delivery failures
from marking schedules as permanently failed. Regression: `tests/unit/test_scheduler_retry.py` (2
cases). Suite **417**.

### Session 17 — engine + versioning + template scan (2026-09-08)
- **XSS BUG (toast innerHTML, fixed commit `93beac3`):** `templates/base.html:71` and `editor.html:739` set
  `messageEl.innerHTML = message;`. Server error detail strings can contain user-controlled data. Fixed by
  switching both toasts to `textContent` — all callers pass plain text, no HTML formatting lost.
- **PDF Paragraph XML injection (fixed commit `920bc0d`):** `pdf.py` passed text content directly to
  reportlab's `Paragraph()`, which interprets XML-like markup (`<b>`, `<img src=...>`, etc.). Fixed by
  escaping all user-controlled strings (report title, header, labels, text content) via
  `xml.sax.saxutils.escape`. Subreport placeholder is hardcoded — no escape needed.
- **Cookie security:** `auth.py:87` sets `current_user_id` cookie with `httponly=False` — readable by JS,
  amplifies every XSS finding by leaking the user's UUID. Fix: `httponly=True` unless JS needs it.
- **Crosstab silent failure:** `renderer.py:228` bare `except: data = []` with no logging — pivot errors produce
  blank crosstabs with zero indication. Fix: add `logger.error()`.
- **LDAP auth silent failure:** `auth.py:141` wraps entire LDAP bind in `except Exception: return None` — server
  timeout indistinguishable from wrong password. Fix: log the exception at minimum.
- **Calculated field column quote injection:** `calculated_fields.py:150` generates `df['{col}']` without escaping
  single quotes in column names — a column like `O'Brien` produces a syntax error → all-None results.
- **Versioning service:** structurally sound. Diff engine, restore, comments, tags all correct. No bugs.
- **Cleanup service:** correct. Aware UTC, own session, graceful degradation. No bugs.
- **Rendering core:** correct. Per-element query execution, DataProcessor application, UUID coercion, error
  isolation. No bugs.
- **Settings in-memory only:** confirmed (round-8d). UI changes don't propagate to separate runner. Larger fix.
- **Alembic migrations:** all 8 scanned, chain correct, upgrades/downgrades idempotent. No bugs.
- **Delivery services (SFTP, SMB, webhook):** re-verified clean after Session 16 round-3 fixes. No new bugs.
- **AI client:** re-verified clean after Session 16 round-6 fixes. Retry logic, error classification correct.
