# Pre-existing Issues & Tech Debt Log

**Purpose:** Track issues discovered out-of-band (not part of the active feature backlog) so they
are not lost. These are NOT in the current phase-list; they are cleanup/hardening to address after
the feature phases (#4–#10) are complete. See `INTERNAL.md` for the feature backlog.

**Scan command:** `.venv/bin/ruff check app/ tests/`
**Last scanned:** 2026-09-02
**Total:** 13 errors (down from 108 on 2026-09-02; 282 before today's ruff.toml). Full history:
549 → 282 (F821/F841/S110/E722/F401/I001/UP*/SIM/PIE/etc. sweep) → 108 (B008+RUF012 suppressed via `ruff.toml`) → 13 (DTZ datetime migration).

**This-session fixes (2026-09-02):** Resolved all *genuine* F821 crashes and all F841s.
F821 genuine: 16 → 7 remaining (7 are SQLAlchemy `Mapped["..."]` forward-ref false positives,
left as-is). F841: 4 → 0. See per-section notes below.

**Follow-up sweep (2026-09-02b):** Fixed remaining safe auto-fixable lint (SIM102/SIM114/SIM117,
PIE810, PERF102, RUF022, FURB188) and removed dead `UserModel` import + renamed duplicate
`get_schedule` (F811). Added project `ruff.toml` suppressing two *intentional* patterns —
B008 (FastAPI `Depends()`/`Body()` route defaults) and RUF012 (mutable class-level state like the
connector registry / `config_fields` / Pydantic `Config.json_encoders`) — with justification in-file.
These are not bugs; fixing inline would break FastAPI DI or misannotate shared state.

---

## Test resources (out-of-band reference — keep so it is not lost)

**Live northwind PostgreSQL** used to validate scheduled exports / real-data integration tests against
real rows. Full details are in `INTERNAL.md` → "Environment & commands". Quick ref:

- Host `10.0.1.33:5432`, database/user/password all `northwind`.
- Consumed by `tests/integration/test_real_data_export.py` (reachability-guarded) and the Session 13
  Docker runner-mode validation. If this server is ever unreachable, those integration tests skip
  rather than fail.

---

## Breakdown by category

### app/ (2026-09-02b state; see Total above for running count)
Remaining **108** errors, all either intentional-pattern suppressions or risky behavioral
changes — none are new genuine crashes:

| Code | Count | Meaning | Status |
|------|-------|---------|--------|
| B008 | 164 | Callable `Depends`/`Body`/`File` in arg default | 🚫 suppressed via `ruff.toml` (intentional FastAPI pattern) |
| RUF012 | 10 | Mutable class attribute | 🚫 suppressed via `ruff.toml` (intentional shared state) |
| BLE001 | 55 | Blind `except Exception` | mostly intentional (`test_connection` returns False); review remaining |
| RUF013 | 22 | Implicit `Optional` return annotation | unsafe fix; leave |
| DTZ003 | 0 | Naive `datetime.utcnow()` in DB-stored fields | ✅ migrated 2026-09-02 (see note below) |
| F821 | 7 | Undefined name | 7 SQLAlchemy forward-ref false positives, left as-is |
| TRY004/TRY002 | 3+3 | Exception-type / custom-exception style | risky (changes documented exception contracts) |
| DTZ005/DTZ011 | 0 | Naive `datetime.now()`/`date.today()` display tokens | ✅ migrated to aware UTC 2026-09-02 |

All previously-listed F401/I001/S110/F841/F811/E722 are now **0** (fixed across sessions).

### tests/
| Code | Count | Meaning |
|------|-------|---------|
| F401 | 11 | Unused import |
| I001 | 6 | Unsorted import block |
| F811 | 6 | Redefinition of unused name |

---

## Highest priority: F821 undefined names (actual crashes)

These raise `NameError` at runtime when the code path executes. **Fixed this session (8 genuine
crashes resolved):**

- `app/services/engine/renderer.py` — `element_label` undefined in `_render_chart`,
  `_render_crosstab`, `_render_image` (confirmed genuine NameErrors at lines 173, 215, 226 —
  these methods reference `element_label` but never receive it as a param; `_render_element`
  only threads it into `_render_table` and, this session, `_render_subreport`). Fixed by threading
  `element_label` through all three (matching the `_render_table`/`_render_subreport` pattern).
- `app/routes/preview.py:623` — `WebSocketDisconnect` not imported. Added to the fastapi import.
- `app/services/delivery/email.py:78` — `self._get_mime_subtype(...)` inside module-level
  `send_email()`. Removed the erroneous `self.` (it's a plain function, no `self`).
- `app/services/delivery/smb_webhook.py:36` — `smbclient.register_session(...)` but only
  `smbprotocol` was imported. Added `import smbclient`. **(Previously mislabeled in this log as a
  lazy-import false positive — it was a genuine missing import.)**
- `app/services/exporters/pdf.py:173,244` — `Image(source)` and `pd.DataFrame()` with no imports.
  Added `from reportlab.platypus.utils import Image` and `import pandas as pd`. **(Previously
  mislabeled here as false positives — genuine missing imports; note pdf.py also can't be imported
  in this env because `reportlab` itself isn't installed.)**

**Remaining 7 are false positives (leave as-is):** SQLAlchemy `Mapped["..."]` forward-ref string
annotations that ruff can't resolve but are valid at runtime:

- `app/models/connection.py:28,91` — `User` (`relationship("User")` string ref).
- `app/models/report.py:33` — `Schedule`.
- `app/models/user.py:42-45` — `Report`, `ReportVersion`, `ReportTag`, `ReportComment`.

**Recommendation:** verify each against actual usage before fixing — the model F821s are
SQLAlchemy forward references that ruff misreads.

## Second priority: S110 silent exception swallowing (7) + E722 bare except (1)

`try/except/pass` and bare `except` hide failures. **FIXED this session (2026-09-02).** The 6
connector `get_schema()` swallows (`mysql`, `odbc`, `postgresql`, `sqlserver`, `rest_graphql` x2)
and the pdf.py chart-render swallow now log via a module-level `logger` instead of `pass`. The
`preview.py:153` bare `except` (in the commit/rollback guard) is now `except Exception:`. Note:
`test_connection()` methods intentionally return `False` on failure (not swallows) and were left
as-is. Remaining S110/E722: 0.

## Third priority: auto-fixable lint (F401 / I001 / B008)

F401/I001 have been swept to ~0 across sessions. B008 (164) is intentionally **suppressed** via
`ruff.toml`, not fixed inline — moving FastAPI `Depends()`/`Body()` out of route defaults would
break dependency injection. Remaining F401/I001 (a handful) can be cleaned with a targeted
`ruff check --fix` later; no urgency.

---

## Files with the most issues (historical — pre-sweep baseline, ~549 errors)

Counts below are from the original scan and no longer accurate; most have been cleared. Kept for
reference on which files tend to accumulate lint. Re-run `.venv/bin/ruff check app/ tests/` for
current per-file counts before any future targeted sweep.

- `app/routes/api/query_builder.py` — 47
- `app/routes/admin.py` — 46
- `app/routes/designer.py` — 38
- `app/routes/datasources.py` — 31
- `app/routes/versions.py` — 28
- `app/routes/preview.py` — 27
- `app/services/query_builder/config.py` — 20
- `app/routes/ai.py` — 20

## Session findings (2026-09-01, feature backlog #8–#10)

Issues discovered while working the feature backlog. Logged here so they aren't lost; still out of
scope for the current phase unless they block a deliverable.

- **Delivery deps not installed.** `asyncssh`, `smbprotocol`, `smbclient`, and `aiosmtplib` are all
  missing from `.venv` (only `httpx` is present). The delivery services therefore cannot run in this
  environment: `import app.services.delivery.email` fails on `import aiosmtplib`. This means SFTP,
  SMB, and email delivery are non-functional here regardless of code. Webhook delivery works
  (uses `httpx`).
- **FastAPI / jose not installed.** The full app cannot be imported in this venv (`ModuleNotFoundError:
  No module named 'fastapi'` / `'jose'`). Integration tests that touch routes/app collection fail to
  import. Only the lightweight engine/service unit tests run (60 passing; ~27 collection errors are
  all `ModuleNotFoundError`, none from feature work).
- **Email test-connection not reachable via UI.** Added `test_connection()` to `email.py` for parity,
  but the create/edit schedule forms only collect `recipient_emails` for email — there is no SMTP
  host/port/user/password config in the schedule form, so an email connection can't actually be
  tested from the UI. Email delivery config (SMTP server) lives elsewhere (global settings?), not on
  the Schedule. Decide whether email testing belongs in this flow or should be skipped.
- **`templates/admin/schedules.html`: duplicate `submitCreateSchedule`.** Two definitions of
  `submitCreateSchedule` (F811 redefinition) — the second shadows the first. Cosmetic but confusing;
  clean up in the lint sweep.

---

## Test coverage added (2026-09-02)

A test-first pass over the dep-free core surfaced and fixed **two real bugs**:

- **`WhereFilter.to_sql()` BETWEEN bug** (`app/services/query_builder/config.py`): the
  `isinstance(self.value, list)` check shadowed the `BETWEEN` branch, so a BETWEEN filter
  (which the parser builds with a 2-element list value) rendered as `IN ('a','b')` instead of
  `BETWEEN 'a' AND 'b'`. Fixed by moving the operator-aware BETWEEN/LIKE checks ahead of the
  generic list→IN branch.
- **BETWEEN dropped during SQL parse** (`app/services/query_builder/sql_parser.py`):
  `parse_sql_to_config()` splits the WHERE body on `AND`/`OR` logic operators, which also split
  the `AND` inside `BETWEEN 'x' AND 'y'`, silently discarding the entire BETWEEN condition (and,
  when it was the only condition, the WHERE clause). Fixed by protecting `BETWEEN ... AND ...`
  with a placeholder before the logic split and restoring it after.

Coverage added: `tests/unit/test_query_generator.py` (SQLGenerator + config `to_sql()`),
`tests/unit/test_connectors.py` (ConnectorFactory registry, per-connector `config_fields`, mock
Northwind schema), and 11 conditional-formatting operator tests in `test_engine.py`. Engine
renderer/data_processor were already covered. Test count 60 → ~117; no regressions.

## Notes

- This scan covers ruff only. A fuller "issues and holes" audit (logic gaps, missing error
  handling, untested paths) is a separate follow-up once the feature phases are done.
- Do NOT fix these before finishing the phase backlog — they are out of scope for current work
  and mixing them in risks scope creep and harder-to-review commits.
- **SCHEDULED-EXPORT DATA GAP — FIXED 2026-09-03 (`40bd276`).** `run_scheduler()` now calls
  `ReportScheduler.load_schedules(db)` before `start()`, and `_execute_report` opens its own
  session, resolves the schedule, and delegates to `execute_report()`. `execute_report()` in turn
  runs `_fetch_element_data()` (already present in `runner.py`), which executes each table/chart
  element's `properties.query` against its `data_source` connection at run time. Validated end-to-end
  in Docker against live northwind: a one-shot schedule produced a valid PDF containing real customer
  rows (Alfreds Futterkiste, Ana Trujillo, …).

   **Remaining gap — tables without column definitions — ADDRESSED 2026-09-03 (Session 13).**
   `_render_table` now auto-derives columns from the DataFrame when a table has a `query` but no
   explicit `columns` def, so exporters get at least one column instead of failing with reportlab's
   "must have at least a row and column". Derived column keys keep their original type (int/str) so
   exporter row lookups match `df.to_dict(orient="records")`. Tests:
   `test_render_table_autoderives_columns_when_missing` / `_preserves_int_column_keys`; engine suite
   37 → 39 passing; `ruff check` clean on changed files. Real designer reports define columns, so this
   only affected hand-edited/legacy definitions.

## Lint sweep status (completed 2026-09-02)

Sweep reduced errors **549 → 13**. All genuine crashes and silent-swallows are fixed. The 13
remaining are intentionally accepted — see the rationale below. Do NOT spend effort chasing these;
they are either false positives (F821) or documented style/contract preferences (TRY002/TRY004).

| Remaining | Count | Why accepted (not fixed) |
|-----------|-------|--------------------------|
| F821 | 7 | SQLAlchemy `Mapped["..."]` forward-ref string annotations — ruff can't resolve them; valid at runtime. |
| DTZ003 | 0 | ✅ Migrated 2026-09-02 — all target columns are `DateTime(timezone=True)` (timestamptz), so aware UTC is correct. |
| TRY004 / TRY002 | 6 | Documented exception contracts (e.g. `template_io` docstring says `ValueError` for malformed payload; callers may couple to it) and custom-exception style preference. |
| DTZ005 / DTZ011 | 0 | ✅ Migrated 2026-09-02 — display tokens now render aware UTC (`{{date.now}}`, `{{date.today}}`). |
| LOG014 / ASYNC230 | 2 | Fixed this session (LOG014 → `exc_info=exc`; ASYNC230 → `asyncio.to_thread(Path.write_bytes)`). |

Suppressions already documented in `ruff.toml`: **B008** (FastAPI `Depends()`/`Body()` route
defaults — idiomatic), **RUF012** (intentional mutable class state: connector registry,
`config_fields`, Pydantic `Config.json_encoders`), **BLE001** (intentional graceful-degradation /
expected-failure returns; ruff 0.16 only accepts `logger.exception()`/re-raise, which would spam
tracebacks on *expected* failures like a wrong password during a manual connection test).

### DTZ datetime migration (completed 2026-09-02)
All 13 `datetime.utcnow()` call sites + all model-column `default=`/`onupdate=` defaults were
migrated to aware UTC (`datetime.now(timezone.utc)`). This was safe because **every** target column
is `DateTime(timezone=True)` (Postgres `timestamptz`), so aware datetimes are the correct type —
not the naive-assume-UTC that asyncpg tolerates. Consequences:

- Fixed a latent **aware-vs-naive `TypeError`** in `api_key.py` (`expires_at < datetime.utcnow()`
  compared an aware DB value against a naive one) and in `portal.py` date-range filters
  (`ReportOutput.generated_at >= from_date` where `from_date` was a naive `fromisoformat`).
  `portal.py` now attaches `timezone.utc` to user-supplied dates lacking an offset.
- Display tokens (`renderer.py` `{{date.now}}` / `{{date.today}}`) and the cache-bust `now()` global
  (`main.py`) switched to aware UTC for system-wide consistency.
- No schema/migration change needed (columns were already tz-aware); only the Python values changed.

### Element config read from wrong location in renderer (fixed 2026-09-02)
Element authoring config is nested under `element['properties']` in saved definitions
(`serializeCanvas` in `editor.html`: `{ type, properties: {...} }`). The previous-session
conditional-formatting fix already read `formatting_rules` from both locations, but
`_render_table`/`_render_chart`/`_render_image`/`_render_crosstab` read their *other* config
keys (`columns`, `sort`, `limit`, `type`/`xField`/`yField`/`title`, `src`/`width`/`height`,
`rowField`/`columnField`/`valueField`/`aggregation`) from the **top level** of the element.
Effect: tables exported with zero columns, images came out blank, charts took the element-type
(`"chart"`) as their `chart_type`, and crosstabs hit the "Missing required fields" path — so
scheduled PDF/HTML/Excel export was broken for every non-table element type. Fixed by reading
each key from `properties` with a top-level fallback (matches the existing `formatting_rules`
pattern). Regression guard: `TestElementRenderers::test_all_elements_read_config_from_nested_properties`
in `tests/unit/test_engine.py`. See also the **scheduled-export data-fetching gap** (now fixed — see below).

### Runner mode scheduler was non-functional (fixed 2026-09-03, commit `40bd276`)

Runner mode (`MODE=runner`, `python -m app.runner`) could not execute scheduled reports. Four
independent bugs, all now fixed:

1. **Jobstore crash on boot (`MissingGreenlet`).** `ReportScheduler.__init__` built a
   `SQLAlchemyJobStore(url=asyncpg_url)`. The app talks to Postgres over **asyncpg** (async engine);
   the sync jobstore tried to open a sync connection and raised
   `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called`. Switched to `MemoryJobStore`
   (in-memory). Schedules are reloaded from the `schedules` table on each runner start via
   `load_schedules()`, so no persistent jobstore is needed.
2. **Wrong DateTrigger import.** `from apscheduler.triggers.date_trigger import DateTrigger` — module
   doesn't exist in apscheduler 3.10.4; corrected to `apscheduler.triggers.date`.
3. **Job fired without its schedule id.** APScheduler 3.x passes only the args you give `add_job()` —
   it does not inject `job_id`. `_execute_report` read `kwargs["job_id"]`, got nothing, and logged
   "Report triggered without a job id; ignoring". Fixed by passing `args=[job_id]` in `add_schedule`.
4. **`_execute_report` was a no-op stub.** It only logged "Report execution triggered". Replaced with
   real logic: resolve the schedule by id, then delegate to `execute_report()` (own DB session, lazy
   import to avoid a circular import with `runner.py`).

**Verification:** Docker compose stack (postgres + runner) boots clean; a one-shot northwind-backed
schedule produced a valid 2650-byte PDF containing real customer rows. Note: the app still only loads
schedules at startup — schedules created/updated while a runner is up won't fire until the runner
restarts (or `load_schedules` is re-invoked). Designer-mode API writes are not pushed to a running
runner. Acceptable for now; note if live schedule updates become a requirement.

**BUG (fixed 2026-09-04, Session 16) — scheduled reports were generated but never delivered.**
`app/services/scheduler/engine.py:_execute_report` called `execute_report(schedule, db)` and discarded
its return value; `deliver_report()` (runner.py) was defined but had **no call site**. Every schedule
with a delivery config (email/SFTP/SMB/webhook) produced a `ReportOutput` row in the DB (visible in the
portal) but never sent anything. Root cause: `_execute_report` opened its own session and never threaded
the output through to delivery. Fixed by capturing `output = await execute_report(...)` and calling a new
`_deliver_scheduled(output, schedule_id, db)` helper that loads the schedule's active `Deliveries` + their
`DeliveryRecipients` (FK order matters in Postgres: Schedule→Delivery→Recipient; ReportOutput/AuditLog also
reference these) and delegates to `runner.deliver_report`. Regression tests in `tests/unit/test_scheduler.py`:
`test_deliver_scheduled_loads_active_deliveries_and_recipients` (filters inactive deliveries/recipients),
`test_execute_report_invokes_delivery` (wiring), `test_execute_report_skips_delivery_when_no_output`. Full
suite 247 passed. Also verified end-to-end against live northwind (`/tmp/e2e_webhook_delivery.py`): a CSV
scheduled export reached a live HTTP endpoint with correct metadata (`format=csv`, proper `file_name`).

---

## Remaining functional gap — Live schedule reload without runner restart — FIXED 2026-09-03 (Session 12)

`runner.py:247 run_scheduler()` calls `ReportScheduler.load_schedules(db)` exactly once, at boot
(`runner.py:256`). After `40bd276` the scheduler uses a `MemoryJobStore`, so schedules live only in
the in-memory job registry until the next runner (re)start. **Consequence:** any schedule created or
edited while a designer-mode/runner is up will not fire until that process restarts. In the standard
single-container `docker compose up` (designer mode runs the app; runner mode runs the scheduler) this
only bites when schedules are edited in one mode while a runner of the other mode is live, or when the
scheduler outlives schedule edits.

**Desired end state:** new/changed schedules are picked up without a restart. Options:
1. Periodic re-sync — a `BackgroundTask`/interval job calling `scheduler.load_schedules(db)` every N
   seconds (simplest; slight lag).
2. Event-driven — call `load_schedules()` (or an incremental `sync_schedule(db, id)`) on schedule
   create/update in the designer-mode API routes (`admin.py` schedule endpoints), so a runner sharing
   the same DB picks up the change on its next tick.
3. DB listener — `LISTEN/NOTIFY` on the schedules table (Postgres) to trigger an immediate reload.

Not urgent (restart-on-demand works today) but worth doing before multi-node/production runner use.
Do **not** rely on restart-on-change as the documented end state.

**Implemented (periodic re-sync, option 1).** `ReportScheduler.sync_schedules(db)` reconciles the
in-memory job registry with the schedules table: it adds every active schedule and updates triggers in
place (`add_schedule` already uses `replace_existing=True`, so cron/`run_at` edits take effect), then
removes loaded jobs whose id is no longer active — including schedules that were deactivated or deleted
from the DB. Non-schedule jobs are protected: only UUID-shaped ids are candidates and ids prefixed
`cleanup_` are skipped, so the runner's daily `cleanup_old_reports` job is never reconciled away.
`runner.run_scheduler()` now calls `sync_schedules` once at startup (before `start()`) and again every
`SCHEDULE_SYNC_INTERVAL_SECONDS` (default 60, new `Settings.SCHEDULE_SYNC_INTERVAL_SECONDS`) inside the
event loop. `load_schedules` is kept as a thin delegating alias for backward compatibility. Tests:
`tests/unit/test_scheduler.py` (add / edit-in-place / deactivate / delete / protect cleanup job). All
engine + scheduler tests pass; `ruff check` clean on changed files.

---

## UI error surfacing — `alert()` noise in designer (Session 13, 2026-09-03)

**Finding:** `templates/designer/editor.html` used ~60 bare `alert()` calls for success, validation,
and error feedback. `alert()` is a blocking modal that steals focus, doesn't distinguish severity, and
gives no server-side trace — poor UX and hard to debug.

**Desired end state:** non-blocking, severity-coded inline notifications; errors persist until dismissed
and are logged server-side; validation feedback shown inline near the relevant control.

**Implemented (Session 13):** added an inline toast system to `editor.html`:
- `showEditorToast(message, type, ms)` — color-coded banner (error/warn/success/info) pinned top-right;
  errors stay until dismissed (`ms=0`), transient messages auto-hide after 5s.
- `logError(context, error)` — centralizes `console.error` and returns the detail string so a caller can
  both log server-side and display it. Non-JSON / list-shaped FastAPI `detail` is joined with `; `.
- Wired the core paths: report save (success + failure w/ server detail), preview gen, data-connection
  create/load/test/delete, calculated-field validation, query test, version restore, image upload.
- `alert()` calls in `editor.html` dropped from 60 → ~5 (remaining are informational: the large JSON
  definition viewer and "copied to clipboard" confirmations).

**Follow-up #1 — admin/index pages (Session 13, 2026-09-03):** Added a shared global toast system to
`templates/base.html` (`showToast`/`hideGlobalToast`/`logToastError`) + CSS in `static/css/main.css`,
so every page extending base gets consistent surfacing. Converted all remaining `alert()` calls in
`schedules.html`, `users.html`, and `designer/index.html` (create/update/delete/import/template flows)
to toasts — success auto-hides after 5s, errors persist until dismissed — with hardened non-JSON
error-body handling (`try/except` defaults). `alert()` across templates dropped from ~60 → 4 (the
intentional informational ones only: large JSON definition viewer + clipboard confirmations).

**Follow-up #2 — server-side payload standardization (Session 13, 2026-09-03):** Reworked the exception
handlers in `app/main.py` so every error returns a consistent payload
`{"detail": ..., "status_code": ...}`. 4xx client errors (auth/not-found/validation/query errors) now
surface their real, app-controlled detail even in production, so toasts show e.g. "Query is required"
instead of a generic "An error occurred". 5xx exceptions (both `HTTPException(500)` and uncaught
`Exception`) keep internals hidden in production and only reveal them when `DEBUG` is on — prevents
leaking `str(exc)` / tracebacks; documented via a docstring explaining the 4xx-vs-5xx rationale.
Tests: `tests/unit/test_exception_handlers.py` (7 cases). Skips cleanly where fastapi/jose/starlette
are absent; `ruff check` clean. **#5 is now complete.**

---

## Functional scan — delivery & engine bugs (Session 13, 2026-09-03)

Started a functional (not just lint) scan after the lint sweep (#11) was closed out — the 13 remaining
ruff errors are confirmed intentional: 7 `F821` are SQLAlchemy `Mapped["..."]` forward-ref string
annotations (verified resolvable via `configure_mappers()`), 6 `TRY002`/`TRY004` are documented
exception-style preferences. So the syntax scan had nothing left; the functional scan targets logic gaps
in untested paths.

**BUG (fixed) — SFTP delivery never sent.** `app/services/delivery/sftp.py::send_sftp`:
- Passed raw `bytes` to `asyncssh`'s `SFTPClient.put_file`, which requires a binary stream or local path;
  every SFTP upload raised and the function returned `False`. Wrapped `file_data` in `io.BytesIO`.
- Separately, the `async with ... put_file` block was over-indented under `if key_filename:`, so password-
  auth deliveries (the common case) skipped the send entirely and returned `True` — a silent no-op worse
  than failure. Restored correct `try`-body indentation.
- Regression test: `tests/unit/test_delivery_sftp.py` injects a fake `asyncssh` and asserts `put_file`
  receives the byte stream. Engine+delivery+scheduler suites green; `ruff` clean.

**LIMITATION addressed (fixed) — query-only tables with no column defs.** `_render_table` returned an empty
`columns` list, which exporters (PDF/Excel/CSV/HTML) require at least one of → reportlab "must have at
least a row and column". Now auto-derives columns from the DataFrame when none are configured, preserving
original key type so exporter lookups match `to_dict(orient="records")`. Tests:
`test_render_table_autoderives_columns_when_missing` / `_preserves_int_column_keys`.

**BUG (fixed) — preview used the wrong connector for non-PostgreSQL sources.**
`app/routes/preview.py::render_report_with_data` resolved the per-element connector via
`connection_config.get('connector_type', 'postgresql')`, but `connection_config` is the connection's
`config` JSONB column, which never contains `connector_type`. Every MySQL/SQL Server/REST/GraphQL/ODBC/
CSV/Excel preview silently fell back to the postgresql connector and failed. Fixed by extracting a shared,
testable helper `resolve_data_source_connector(db, definition)` in `app/services/connectors/base.py` that
selects the connector from the `DataConnection.connector_type` **model column** (not the config dict).
Preview and the runner now resolve connectors through one code path. Regression tests:
`tests/unit/test_connector_resolution.py` (5 cases, incl. the model-vs-config guard). ruff clean.

## Connector scan — REST/GraphQL auth & headers (Session 16)

**BUG (fixed) — REST bearer/basic auth was never sent.** `app/services/connectors/rest_graphql.py::
RESTAPIConnector._make_request` read auth from a nested `config["auth"]["type"]` / `config["auth"]["token"]`,
but the connection form stores auth as **flat keys** (`auth_type`, `auth_token`, `auth_username`,
`auth_password`) — see `config_fields` and `editor.html` (`config[f.name] = el.value`). The nested `auth`
object never exists, so `Authorization` was omitted on every authenticated REST call (silent 401s /
unauthenticated access). Fixed to read the flat keys.

**BUG (fixed) — GraphQL requests crashed on any config.** `GraphQLConnector._make_request` did
`{"Content-Type": "application/json", **config.get("headers", {})}`. The GraphQL `headers` field is a
textarea serialized as a JSON **string** (`""` when empty), so `**""` raised
`TypeError: 'str' object is not a mapping` on *every* request — GraphQL preview, test-connection, and
execution were all 100% broken. Fixed by adding a module-level `_coerce_json_field()` helper that normalizes
the textarea JSON string (or an already-parsed dict) to a dict; used for REST `headers`/`params` and
GraphQL `headers` too. Verified live against local HTTP servers: bearer/basic tokens and custom headers now
reach the server; GraphQL requests succeed with empty and populated headers. Regression tests in
`tests/unit/test_rest_connector.py` (4 cases). Full suite 251 passed; ruff clean.

**BUG (fixed) — scheduled exports ignored output_format (always PDF).** `runner.py::execute_report`
hardcoded `PDFExporter()` + `format="pdf"`, so a schedule configured for Excel/CSV/HTML still produced a
PDF. This is the *only* rendered-export call site in the app (no on-demand export endpoint exists). Fixed by
adding an exporter factory to `app/services/exporters/__init__.py` (`get_exporter` / `export_report` /
`normalize_output_format` / `get_mime_type` / `get_file_extension`) that dispatches on the normalized format
and normalizes HTML's `str` return to bytes for the `ReportOutput.file_data` BYTEA column. PDF is imported
lazily so the package imports without reportlab. Native factory tests: `tests/unit/test_exporters_factory.py`
(14 cases; CSV/HTML + pure logic; Excel/PDF runtime verified in Docker 2026-09-03 against live northwind
`customers` table: pdf=%PDF-1.4 (2120 B), xlsx=ZIP magic (5469 B), csv has real headers+rows (171 B) — all
non-empty and matching `schedule.output_format`). Schedule `output_format` now round-trips end-to-end.

**BUG (fixed) — one-shot schedules re-fired on every sync cycle (B5).** `run_scheduler()` calls
`sync_schedules()` every `SCHEDULE_SYNC_INTERVAL_SECONDS` (default 60). One-shot schedules (`run_at`, no cron)
were never deactivated after firing, so each periodic sync re-added them with `replace_existing=True`.
APScheduler fires a past `DateTrigger` immediately within misfire grace, so a completed one-shot re-executed
repeatedly (empirically verified: 1→2→3 fires across three syncs). Added pure helper `is_past_one_shot(schedule)`
and skipped past one-shots in `sync_schedules`; recurring cron schedules unaffected. Tests:
`test_scheduler_oneshot.py` (6 cases).

**HOLE (partially fixed) — exporters dropped non-table elements.** `PDFExporter` renders text/table/chart/
image/subreport, but `HTMLExporter`/`ExcelExporter`/`CSVExporter` only rendered `text` and `table` — a chart-
only or chart-bearing report exported to HTML produced an empty body while PDF kept the chart. Root cause: the
exporters' element loops had no branch for `chart`. Fix: `HTMLExporter._render_chart_html()` now plots the
element's attached DataFrame via `ChartGenerator` and base64-embeds the PNG inline (`data:image;base64,`),
mirroring the PDF path. Excel/CSV remain intentionally tabular (charts don't translate; documented). Native
test: `test_html_export_inlines_chart_as_base64_png` (in `test_exporters.py`). Full pipeline verified via
`ReportRenderer` → `HTMLExporter`. Note: the PDF chart path was *also* broken (see B6 below) — this hole
fix only covers HTML; PDF needed a separate crash fix.

**BUG (fixed) — PDF export crashed on any chart (B6).** `PDFExporter._render_chart` appended a raw
`reportlab.utils.ImageReader` to the flowable story. A bare `ImageReader` lacks `getKeepWithNext` and
renders with an unknown height, so reportlab raised `... too large on page 2 in frame 'normal'` for every
chart-bearing report (the HTTP-source branch of `_render_element`'s image handler had the same flaw). Added
`PDFExporter._scaled_chart_image()`: reads the PNG width/height from the IHDR header (ChartGenerator always
emits PNG) and builds an `Image` with explicit scaled width/height fitting the 480pt content area.
Docker-verified against live northwind: a table+chart report now exports a valid 56KB PDF (was failing) and a
valid XLSX.

**BUG (fixed) — Excel connector silently ignored params/query without pandasql (B7).**
`ExcelConnector.execute_query` ran `pandasql.sqldf(query)` inside a try that caught `ImportError` with a bare
`pass`. Since `pandasql` is not installed in the minimal/test env (or any fresh install), both the SQL query
*and* the parameters were dropped and the full unfiltered sheet was returned. Now parameter filtering is applied
manually (matching `CSVConnector`) and a warning is logged when a SQL query can't be executed. Native tests:
`test_csv_excel_connectors.py` (4 cases; Excel path monkeypatches `pd.read_excel` since openpyxl/xlsxwriter are
absent natively).

**Enhancements applied (scan §3 should-haves):** schedule create/update now validate `output_format` up front
(admin.py) — a bad value returns HTTP 400 instead of being stored and only failing at run time; and
`execute_report` records the produced format/mime_type/file_size in the `report_generated` audit event
(runner.py).

**New feature (scan §4): on-demand export.** `GET /portal/reports/{id}/export?format=` renders a saved
report against live data and streams pdf/xlsx/csv/html. Shared render/fetch core extracted to
`app/services/report/rendering.py` (`fetch_element_data` + `render_report_bytes`) so both the runner and the
route use one native-tested path without pulling the runner's delivery-stack imports (asyncssh/smbclient/
aiosmtplib) into route handlers. Native tests: `test_rendering.py` (5 cases). Docker-verified vs live
northwind: all four formats return real data; 401/400/404 paths confirmed.

**BUG (fixed) — calculated fields with whitespace in `{{ }}` evaluated to null in preview/export.**
`DataProcessor._evaluate_expression` matched column refs with a `\w+-only` regex (`{{col}}`), so natural
expressions like `{{ revenue }} - {{ cost }}` produced all-None columns on the preview/scheduled-export path.
The Fields-tab Test path (`CalculatedFieldEvaluator`) used a lenient `[^}]+` regex and handled whitespace, so
the two paths silently diverged. Delegated `DataProcessor` to the shared `CalculatedFieldEvaluator` so both
paths stay in sync; added regression test in `test_engine.py`.

**BUG (fixed) — calculated fields never applied on the scheduled/on-demand EXPORT path.** Round-trip scan
(Session 14) of `serializeCanvas` ↔ renderer: `DataProcessor.process()` was called in exactly one place,
`app/routes/preview.py:179` (live preview + Fields-tab Test). The export core — `fetch_element_data` in
`app/services/report/rendering.py`, which both `runner.execute_report` and the on-demand route share — ran each
element query straight into a DataFrame with **no** calculated-field/grouping step. So any `calculated_fields`
stored in the saved definition were silently dropped from every exported/scheduled report while working fine in
live preview. Fixed by applying `DataProcessor().process(df, definition)` per table element inside
`fetch_element_data` (guarded to non-empty DataFrames and table elements only, mirroring preview; charts left
untouched to match preview exactly). Now both paths compute calculated fields identically. Regression:
`tests/unit/test_definition_roundtrip.py` (2 tests) asserts the computed field flows through
`fetch_element_data` → `ReportRenderer` → CSV exporter (`qty*2` values present in output). `ruff check` clean;
full suite 199 passed / 0 failures (26 pre-existing fastapi/jose collection errors unrelated).

**BUG (fixed) — every chart emitted a matplotlib legend warning + empty legend box.** `app/services/engine/chart.py:45`
called `ax.legend()` unconditionally, but no series carry labels (single-series bar/line/scatter/pie), so
matplotlib logged `UserWarning: No artists with labels found to put in legend` on **every** chart and rendered
a blank legend handlebox in the corner. Removed the unconditional `ax.legend()` call; all four chart types
still emit valid PNGs. Warning no longer appears in the suite warnings summary.

**BUG (fixed) — email CSV/HTML attachments used wrong MIME maintype (commit `cd341c4`).** `send_email`
wrapped every attachment in `maintype="application"`, so CSV/HTML reports were sent as
`application/csv` / `application/html`. Clients fail to open those correctly; PDF/XLSX were fine. Derive
maintype from subtype (`text/*` for csv/html, `application/*` otherwise). Native regression tests inject a
fake `aiosmtplib` and assert the attachment part's content type for all four formats.

**BUG (fixed) — WHERE filters ignored per-filter AND/OR logic (commit `3653789`).** `QueryConfig.to_sql()`
joined every WHERE clause with the *first* filter's `logic`, so multi-condition queries rendered as
`... AND ... AND ...` regardless of individual OR/AND selectors — silently altering semantics for every
connector that builds SQL via the query builder. Each filter now joins with its own operator; also removed
dead duplicate LIKE/BETWEEN branches in `WhereFilter.to_sql()`. Regression tests in `test_query_generator.py`.

**BUG (fixed) — SQL injection / broken literals in generated WHERE values (commit `ee33db0`).**
`WhereFilter.to_sql()` wrapped values in unescaped single quotes, so apostrophes in data (e.g. `O'Brien`)
produced syntactically broken SQL and untrusted input could escape the literal (`' OR '1'='1` → injection).
All string literals now go through a shared `_quote()` helper that doubles embedded quotes per the SQL
standard. Regression tests cover `=`, `LIKE`, `BETWEEN`, `IN`, plus an injection attempt.

**BUG (fixed) — version diff missed changes in duplicate section types (commit `c6b2e6a`).**
`ReportDiffEngine` keyed sections by `type -> single index`, so a report with multiple sections of the same
type (e.g. extra Detail bands, which the designer allows via repeated `addSection`) collapsed to the last one
and dropped changes to earlier duplicates from the version history diff. Now groups indices by type in document
order and pairs them; excess types report as added/removed. Regression tests in `test_versioning.py`.

**BUG — duplicate column names dropped in `execute_query` results — FIXED 2026-09-04 (Session 14).**
`adapter.execute_query()` builds result rows with `dict(zip(names, row))`. When a query selects two columns
with the same name across tables (e.g. `SELECT a.id, b.id FROM a JOIN b ...`, or any pair of `id`/`name`/
`created_at` columns), the dict collapses duplicates and silently drops every value but the last. Fixed at
the source so materialization can no longer drop anything from builder-generated SQL.

**Fix (query-builder generation layer).** Added `SelectColumn.base_name()` (bare column name, or synthetic
`{func}_{table}.{column}` for aggregates) and a module-level `resolve_select_names(columns)` in
`app/services/query_builder/config.py`. It walks the select list, keeps explicit aliases immutable, and on a
collision qualifies the later column as `table__name` (then `table__name_2`, …) so every selected column gets
a unique result key. Wired into both `QueryConfig.to_sql()` and `SQLGenerator.generate_select()`: a column is
emitted bare only when its resolved name equals its `base_name()`; otherwise `expr AS <name>` (explicit aliases
always emit `AS`). Result: `SELECT a.id, b.id` → `SELECT a.id, b.id AS b__id`; no-collision selects are
byte-for-byte unchanged.

**Why no renderer/adapter coordination was needed.** Traced every `to_sql()` consumer — all live in
`routes/api/query_builder.py` (`/test`, `/validate`, `/generate-sql`, `/nl-to-query`), and none feed a
field-lookup renderer: `/test` returns raw rows that the frontend iterates by column name, so qualified names
are merely *more* readable. The export/schedule path runs **raw** `properties.query` SQL through
`connector.execute_query` (pandas DataFrame keyed by whatever the DB returns), so it is outside the builder's
generation layer by design.

**Scope boundary.** This fixes structured QueryBuilder SQL (the documented "JOIN patterns the builder
generates most"). A report whose stored `properties.query` is raw, hand-authored, or pre-dates this fix and
still contains bare colluding names (`SELECT a.id, b.id`) is unaffected — the correct SQL there is to alias
the joined columns, which the builder now does automatically on regeneration. No connector/materialization
change was required; unique output names make `dict(zip(...))` lossless by construction.

**Regression tests:** `tests/unit/test_query_generator.py` — `test_generate_select_disambiguates_duplicate_plain_columns`,
`_three_way_collision`, `test_generate_select_respects_explicit_alias_over_collision`,
`test_generate_select_no_collision_unchanged`, `test_query_config_to_sql_aliases_join_collisions`,
`test_resolve_select_names_helper`. Builder suite green; `ruff check` clean on changed files.

**Minor findings (defer to cleanup §5):** RESOLVED (commit `6b5bf0e`). The 3 bare `raise Exception` in
`rest_graphql.py` (REST non-200, GraphQL non-200, GraphQL errors array) are now `ConnectorError`, a new
`Exception` subclass so callers can catch connector failures specifically. Ruff clean; native REST connector
tests still pass.

**Round-trip scan DONE (Session 14): designer save/load (serializeCanvas ↔ renderer).** Backend half verified
with `tests/unit/test_definition_roundtrip.py` using a definition shaped exactly as `serializeCanvas()` emits
it (per-element `properties.query`/`columns`, top-level `data_sources`/`calculated_fields`). `normalize_report_definition`
is non-lossy (preserves all keys). Observed (deferred — not a regression, exporter scope is a product decision):
`CSVExporter`/`ExcelExporter` only render tables in sections whose `type == "detail"`
(`excel_csv_html.py`), while `HTMLExporter` renders every section. A data table placed in a header/footer/summary
section is dropped from CSV/XLSX but shown in PDF/HTML. Flagging so we decide intentionally whether tabular
exporters should iterate all section types. Route/integration layer still unscanned (26 route tests can't run
here — need fastapi/jose). SMB/webhook/email send paths
reviewed: `send_smb` writes bytes correctly, `send_webhook` passes bytes via `httpx(content=...)`, email
covered by `test_delivery_email.py`.

---

## Architecture discussion (future session) — ORM vs. direct connections for multi-DB support

**Question raised:** With PostgreSQL, MySQL, SQL Server, ODBC, CSV, Excel, REST, and GraphQL all supported,
should we adopt an ORM backend instead of direct per-connection drivers to simplify the frontend (`preview.py`)
and scheduler (`runner.py`)?

**Current architecture (as of Session 13):**
- Connectors implement a common `DataConnector` ABC (`execute_query`, `get_schema`, `test_connection`) on top of
  dialect-specific async drivers (asyncpg, asyncmy, pymssql, odbc, pandas, httpx).
- The query builder emits **raw SQL strings**; connectors execute them against their DB. SQLAlchemy is used only
  for the app's *own* models (users/reports/schedules) via its async engine — not for user report queries.
- Frontend and scheduler both call `connector.execute_query(config, sql)` uniformly through `get_connector()`.

**Assessment — an ORM would NOT fundamentally simplify this, and could complicate it:**
1. **The connector ABC is already the abstraction layer.** Uniform `execute_query(config, sql)` means frontend and
   scheduler are already DB-agnostic; adding an ORM underneath wouldn't change that call site meaningfully.
2. **ORMs optimize for known schemas, not arbitrary user SQL.** Report tools execute dynamic, user-authored queries
   against unknown schemas. ORM query-building (SQLAlchemy Core/ORM) shines when tables map to Python classes up
   front — the opposite of a free-form query builder. You'd still generate raw SQL for user queries; the ORM would
   just sit below your current layer without replacing it.
3. **You're already using SQLAlchemy the right way** — as the async engine/ORM for *app* models, raw async drivers
   for *user data*. Splitting those concerns is correct.

**Where an abstraction WOULD help (and what to consider instead of an ORM):**
- The real multi-DB pain is **SQL dialect normalization** — pagination (`LIMIT` vs `OFFSET/FETCH` vs `TOP`), date
  functions, quoting, boolean/null literals differ across Postgres/MySQL/SQL Server. This is a *SQL translation*
  problem, not an ORM problem.
- A SQL parser/translator like **SQLGlot** (dialect-aware parse → rewrite) could normalize generated SQL across
  dialects in one place, without pulling in ORM model mapping. More targeted fit than an ORM for a query-builder tool.
- **Recommendation:** defer. Only adopt if multi-DB correctness bugs actually surface (e.g. the deferred duplicate-
  column / placeholder findings). Prematurely adding SQLGlot or ORM mapping would increase dependency surface and
  complexity before the dialect-difficulty is proven to warrant it. Current per-driver connectors are the pragmatic
  choice for a report tool with arbitrary user SQL.

---

## Authz sweep — query-builder API was unauthenticated (Session 16, 2026-09-04)

**Finding:** `app/routes/api/query_builder.py` defined its own auth dependency:

```python
async def get_current_user_simple(request: Request) -> User | None:
    """Simple current user dependency without database."""
    return None
```

and wired it into **all 16** of its routes via `Depends(get_current_user_simple)`. Every other router in the app
uses `get_current_user_optional` (decodes the JWT/cookie and returns the real `User`, or `None`). Because this stub
always returned `None`, no route enforced authentication — including `/test` (raw SQL execution against a live DB
connection), `/schema` (DB schema introspection), template `save`/`import`, and the AI `nl-to-query` endpoint. The
two routes that did reference `current_user` only used it for `created_by` scoping, which silently fell back to
`None`.

**Fix:** replaced the stub with `require_auth()`, which delegates to `get_current_user_optional` and raises
`HTTP 401` when no user is present — matching the pattern used by `admin.py`/`datasources.py`/`designer.py`.
`current_user` is now populated for the `created_by` scoping writes. The SPA reaches these endpoints from the
authenticated designer page, so the browser auto-sends the `access_token` cookie and nothing breaks client-side.

**Related hardening:** `preview.py::preview_websocket` resolved `current_user` but called `websocket.accept()`
before checking it, so an unauthenticated client could open an echo channel against any `report_id`. Added the
standard 401 guard before accept. The channel is echo-only (no data exposure) and has no frontend consumer or test.

**Verification:** a static scan of every route confirms all others enforce auth (via `if not current_user` or
`_require_designer`, which also role-checks to admin/designer). Only `login`/`logout` are correctly public.
`test_template_import.py` (which exercised these routes over an unauthenticated client) migrated to the `auth_client`
fixture; added `test_export_requires_authentication` asserting a 401. Full suite **252 passed**; ruff clean on all
changed files. The 7 remaining `F821` ruff findings are pre-existing SQLAlchemy `Mapped["..."]` forward-ref false
positives in the models (connection.py/report.py/user.py), not introduced here.

## Portal output visibility parity (Session 16, 2026-09-04)
`portal.py::portal_index` scoped executed outputs to `generated_by == user` OR reports the user owns a
schedule for — admins could not oversee executions created by others, breaking parity with the report-catalog
fix in `designer.py::list_reports`. Admins now see every output; non-admins keep schedule-ownership scoping.
Tests: `tests/integration/test_portal.py` (2).

## `generated_by` population on scheduled outputs (Session 16, 2026-09-04)
`runner.py::execute_report` created `ReportOutput` rows without setting `generated_by`, so the portal's
`generated_by == user` clause matched nothing and non-admin visibility depended solely on schedule ownership.
Now sets `generated_by = schedule.owner_id` (the schedule's owner is the attributing user). Regression test:
`test_scheduler.py::test_execute_report_attributes_output_to_schedule_owner` — runs the real `execute_report`
with `_fetch_element_data`/`export_report` stubbed and asserts `output.generated_by == schedule.owner_id`.
Full suite **261 passed**. This completes the Phase 4 visibility follow-up noted in `INTERNAL.md`.

## Subreport export gap + report visibility by role (Session 16, 2026-09-04)

**Bug — subreports silently dropped from HTML/CSV/XLSX exports.** `PDFExporter` rendered inline subreports
(recursing into `layout.elements`), but `HTMLExporter`/`ExcelExporter`/`CSVExporter` only handled `text` and
`table`, so any report containing a subreport lost that content on non-PDF export with no indication. Fixed by
adding `_render_element_html`/`_render_subreport_html` (HTML), `_write_subreport`/`_write_table(sheet_name=...)`
(Excel — unique sheet names avoid the default `Sheet1` collision for nested tables), and
`_table_to_csv_buffer`/`_append_subreport_csv` (CSV). Non-inline (`drill_down`/`page`) modes now emit an ASCII
placeholder instead of vanishing. Regression tests in `tests/unit/test_exporters.py::TestSubreportExport`
(4 cases). Full suite **258 passed**.

**Finding — report catalog scoped admins to their own reports.** `designer.py::list_reports` restricted
*everyone* (admins included) to `created_by == current_user.id` when no filter was applied, so admins could not
oversee reports created by others. Non-admins correctly keep the "own reports only" default; admins now see the
full catalog. Fix + tests (`test_admin_sees_all_reports`, `test_designer_sees_only_own_reports`) in
`tests/integration/test_api.py`. **Portal output parity ALSO complete:** `portal.py::portal_index` now gives
admins an unscoped `select(ReportOutput)` while non-admins keep the `generated_by == user` / owner-schedule
filter — mirroring the catalog fix. Verified by `tests/integration/test_portal.py` (2 tests). The earlier
"follow-up still open" note in this log was stale and has been corrected here. Documented as "Phase 4: Access
Control & Permissions" in `INTERNAL.md`.

## Calculated-field code injection via eval() (Session 16, 2026-09-05)
**Severity: High (CWE-94).** `app/services/engine/calculated_fields.py::CalculatedFieldEvaluator.evaluate`
passed user-authored expressions to Python `eval()` with only an empty `__builtins__` dict. That sandbox is
trivially escapable (`().__class__.__bases__[0].__subclasses__()` reaches 162+ classes → arbitrary code exec),
and **neither call site validated the expression first**: the Fields-tab Test endpoint
(`datasources.py`) and the preview/scheduled-export path (`data_processor._add_calculated_fields`). Expressions
reach the export path via stored report definitions, so a malicious `calculated_fields` entry (e.g. imported
through template import) executes on every scheduled run. **Fix:** replaced `eval()` with an AST-based evaluator
(`_SafeExpressionEvaluator`) permitting only `df['col']` lookups, arithmetic/comparison operators, and a
whitelist of pure functions; all attribute access / unlisted calls / comprehensions raise and evaluate to null.
Regression tests in `test_engine.py::TestCalculatedFieldEvaluator` (parametrized injection cases). Full suite
**267 passed**. **Follow-up:** the Fields-tab Test endpoint still evaluates an unvalidated expression typed by
the user before it is stored — acceptable now that eval is sandboxed, but consider surfacing `validate_expression`
errors inline instead of silently returning nulls.

## SQL parser drops per-condition AND/OR logic (Session 16, 2026-09-05)
`sql_parser.py::parse_sql_to_config` split the WHERE body on AND/OR but tagged **every** condition with the
*last-seen* logic operator instead of its own preceding one. A mixed clause `A AND B OR C` therefore
round-tripped as `A OR B OR C`, silently altering query semantics whenever SQL is parsed back into an editable
`QueryConfig` (AI-generated or pasted SQL). Fixed by pairing each condition with the operator that precedes it
from the alternating `split()` result. Also removed a dead shadowed `_OPERATOR_PATTERN` definition. Regression:
`test_sql_parser.py::test_where_preserves_per_operator_logic`. Full suite **268 passed**; ruff clean.

## Query-builder test preview drops duplicate columns (Session 16, 2026-09-05 — logged, not fixed)
`adapter.execute_query()` materializes rows with `dict(zip(names, row))` (SQLite/MySQL) and `dict(r)`
(asyncpg). When a query selects same-named columns from different tables (e.g. `SELECT a.id, b.id`), the
duplicate keys collapse and only the last value survives. This affects **only** the query-builder "Test
Query" preview (`query_builder.py::test_query_endpoint`, returns `rows[:10]`); the report export path uses
pandas DataFrames which preserve duplicate columns, so scheduled/on-demand exports are unaffected. Fixing it
would change the `execute_query` return contract (list[dict] cannot hold dup keys) across all three backends
and their tests — deferred as a low-priority, scope-expanding change.

## Dead code noted
- `data_processor.py::filter_data()` has no callers (only defined, never invoked). Safe to remove in a
  cleanup pass; not a bug.
- `scheduler/engine.py::log_audit()` is defined but never called — inline `AuditLog` writes in
  `runner.execute_report` are the actual path.

## Mokapi test server — removed (Session 17, 2026-09-08)
**Decision:** drop all mokapi references. The `mokapi/mokapi` docker image was never configured for
testing — LDAP and SMTP were both unwired. All mokapi artifacts (`docker-compose.yml` service,
`test-assets/mokapi/`, `.env.example` LDAP config, `settings.html` testing section, `USE_MOKAPI`
setting) have been removed. LDAP/email test infrastructure will be handled in a separate standalone
project if needed.

## Separate-mode scheduler — dropped (Session 17, 2026-09-08)
**Decision:** drop the "dedicated runner" concept entirely. The app runs as a single container in
designer mode with the scheduler embedded via `SEPARATE_MODE=false` (the default). All references to
running designer and runner as separate containers have been removed from documentation and config.
`SEPARATE_MODE` has been removed from `Settings`; the scheduler always starts in the designer process
(`main.py:startup_event`). Schedules are managed through the admin UI (`/admin/schedules/`) and
executed by the embedded scheduler — no second container needed.

---

## Session 17 — Engine + versioning + template scan (2026-09-08)

### BUG (XSS, fixed 2026-09-08, commit `93beac3`) — Toast `innerHTML` executes user-controlled HTML
The global toast system in `base.html` and the editor toast in `editor.html` both set
`messageEl.innerHTML = message;`. Server-side error handlers return `{"detail": str(exc)}` which can
contain user-controlled data (e.g. column names, file paths, connection strings). If an exception
message reaches the toast via `logToastError()` → `showToast()`, embedded `<script>` or event handlers
execute in the designer's browser context. **Impact:** stored XSS through any error path that surfaces
to the toast (schedule failures, connector errors, query errors). The preview/export XSS fixes
(round-8f/8g) escaped data in rendered HTML but the toast system was a separate injection surface.
**Fix:** switched both `showToast` (base.html) and `showEditorToast` (editor.html) to
`messageEl.textContent = message;`. All callers pass plain-text strings — no HTML formatting lost.
Committed `93beac3`.

### BUG (fixed 2026-09-08, commit `920bc0d`) — PDF Paragraph XML injection
`PDFExporter._render_element` passed text element content directly to
`reportlab.platypus.Paragraph(content, style)`. Reportlab's Paragraph interprets XML-like markup
(`<b>`, `<i>`, `<font>`, `<img src=...>`, `<link>`, etc.). A text element whose `content` is
populated from untrusted data (e.g. imported templates or AI-generated reports) could inject
flowables — including `<img src=file:///etc/passwd>` for local file read or `<anchor>` for layout
disruption. **Fix:** escaped all user-controlled strings passed to `Paragraph` using
`xml.sax.saxutils.escape`: report title, header canvas text, element labels, and text content.
The subreport placeholder is hardcoded and does not need escaping. Committed `920bc0d`.

### BUG — `current_user_id` cookie readable by JavaScript (`app/routes/auth.py:87`)
The login response sets `current_user_id` with `httponly=False`, making it accessible via
`document.cookie`. Combined with any XSS vector (including the toast `innerHTML` bug above), an
attacker can read the authenticated user's UUID and use it for targeted API calls. **Impact:**
amplifies every XSS finding by leaking the user's stable identifier. **Fix:** set `httponly=True`
unless JavaScript actually needs to read this cookie (check all JS consumers first).

### HOLE — Crosstab silent failure with no logging (`renderer.py:228`)
`_render_crosstab` wraps `pd.pivot_table()` in a bare `except Exception: data = []`. Any pivot
failure (missing column, type mismatch, empty group) produces an empty crosstab with **no log
message** — the designer sees a blank report section with no indication of what went wrong.
**Impact:** debugging crosstab reports requires adding manual logging. **Fix:** at minimum log the
exception via `logger.error()` before returning empty data.

### HOLE — LDAP auth failure indistinguishable from wrong password (`auth.py:141`)
`authenticate_ldap_user` wraps its entire body in `except Exception: return None`. An LDAP server
timeout, network error, or misconfiguration returns the same `None` as an invalid password, so the
login form shows "Incorrect email or password" regardless. **Impact:** admins cannot diagnose LDAP
connectivity issues from the login UI; users get no actionable feedback. **Fix:** log the exception
at minimum; consider returning a distinct error for infrastructure failures vs. auth failures.

### HOLE — Calculated field column names with single quotes break AST (`calculated_fields.py:150`)
`_process_expression` replaces `{{col}}` with `df['{col}']`. If a DataFrame column name contains a
single quote (e.g. `O'Brien`), the generated expression becomes `df['O'Brien']` — a syntax error
that the AST parser rejects, returning all-None. **Impact:** reports pulling data from sources with
quoted column names silently produce null calculated fields. **Fix:** escape single quotes in column
names before generating the replacement string (e.g. `col.replace("'", "\\'")`).

### NOTE — Settings in-memory only, no cross-process propagation (`settings.py:72-90`)
Already noted in round-8d. Confirmed: `update_settings` mutates the pydantic `Settings` singleton
in-process. A separate runner container reads settings from environment at startup and never sees
UI changes. SMTP/LDAP/AI edits require a runner restart to take effect. **Not fixed** — requires a
settings table + reload hook; larger architectural change.

### NOTE — Database session redundant close (`database.py:24`)
`get_db()` uses `async with async_session_factory() as session:` (which handles commit/rollback)
and then `await session.close()` in the `finally` block. The context manager already closes the
session on exit. The explicit `close()` is harmless but unnecessary. **Cleanup only.**

### NOTE — Chart axis labels from definition, not data (`chart.py:44,56-76`)
`ax.set_title()`, `ax.set_xlabel()`, `ax.set_ylabel()` use values from the chart definition
(`chart_def.get("title")`, `x_field`, `y_field`). These are designer-authored (trusted), not
live data. No escaping needed here — matplotlib renders them as plain text, not HTML. **Verified clean.**

### NOTE — Versioning service structurally sound (`versioning/*.py`)
Scanned `diff.py`, `store.py`, `restore.py`, `comments.py`, `tags.py`. The diff engine correctly
handles duplicate section types (fixed round-7c). `restore_version` creates a new version entry
rather than overwriting (append-only history). `delete_comment` scopes to comment owner.
`add_tag` enforces uniqueness per report. `remove_tag` has no ownership check — any user with
report access can remove tags (consistent with the route-level authz from round-7c). **No bugs.**

### NOTE — Cleanup service correct (`cleanup.py`)
`cleanup_old_outputs` uses aware UTC datetime, opens its own session, deletes by cutoff.
`send_failure_notification` defaults to `SMTP_FROM` if no notify emails, gracefully degrades
when aiosmtplib is absent. **No bugs.** Confirmed the wiring from round-5 works correctly.

### NOTE — Rendering core correct (`report/rendering.py`)
`fetch_element_data` executes per-element queries against the primary connection, applies
`DataProcessor.process()` for table elements (calculated fields + grouping), and handles
connection_id UUID coercion. Error paths log and skip individual elements without aborting the
whole report. **No bugs.**

### NOTE — Alembic migrations structurally sound (Session 17)
Scanned all 8 migration files. Chain: `f4393eaac236` (initial) → `57a68b59befa` (api_keys) →
`eb7ad9503220` (schedule output/delivery) → `5d118a82febb` (owner_id) → `a1b2c3d4e5f6`
(query_templates) → `f6e5d4c3b2a1` (query_history) → `c7d8e9f0a1b2` (report_templates). Separate
branch: `b2c3d4e5f6a7` (hash api_keys, revises `a1b2c3d4e5f6`). All upgrades/downgrades are
correct and idempotent. The hash migration uses a sync connection (`op.get_conn()`) which is the
proper alembic pattern. **No bugs.**

### NOTE — Delivery services (SFTP, SMB, webhook) re-verified clean (Session 17)
Re-scanned `sftp.py`, `smb_webhook.py` after Session 16 round-3 fixes. SFTP correctly wraps bytes
in `io.BytesIO` for `put_file`. SMB UNC path construction is correct. Webhook HMAC signing matches
the documented format (`t=<unix>,v1=<sha256>`). All test-connection guards work. **No new bugs.**

### NOTE — AI client re-verified clean (Session 17)
Re-scanned `client.py` after Session 16 round-6 fixes. Retry logic correctly handles transient
statuses (408, 429, 5xx) with exponential backoff. `_extract_json_response` strips markdown fences
and falls back to balanced-brace extraction. `classify_ai_error` maps response errors → 502,
transport errors → 503, other → 500. **No new bugs.**

## Session 17 round-2 — remaining bugs from Session 17 scan (2026-09-08)

### BUG (fixed) — `current_user_id` cookie `httponly=False` (`app/routes/auth.py:87`)
The login response sets `current_user_id` with `httponly=False`, making it accessible via
`document.cookie`. Combined with any XSS vector, an attacker can read the authenticated user's UUID
and use it for targeted API calls. **Fix:** set `httponly=True` on both cookie writes in `auth.py`.

### HOLE (fixed) — LDAP auth failure indistinguishable from wrong password (`app/auth.py:141`)
`authenticate_ldap_user` wrapped its entire body in `except Exception: return None`. An LDAP server
timeout, network error, or misconfiguration returns the same `None` as an invalid password, so the
login form shows "Incorrect email or password" regardless. **Fix:** added `logger.error("LDAP authentication
failed for %s: %s", email, e)` before returning None. Regression: `tests/unit/test_auth.py::TestLdapAuthLogging::test_ldap_exception_is_logged`
(monkeypatches `app.auth.logger` and asserts `error()` is called with the email + exception).

### EDGE CASE (fixed) — Calculated field column names with single quotes break AST (`calculated_fields.py:150`)
`_process_expression` replaces `{{col}}` with `df['{col}']` verbatim. If a DataFrame column name contains
a single quote (e.g. `O'Brien`), the generated expression becomes `df['O'Brien']` — a syntax error that
the AST parser rejects, returning all-None. **Fix:** escape single quotes in column names before generating
the replacement: `col.replace("'", r"\'")`. Regression: `tests/unit/test_engine.py::test_process_expression_escapes_single_quotes_in_column_names`
(asserts `{{ O'Brien }} - {{ cost }}` evaluates correctly when the DataFrame has an `O'Brien` column).

### STALE TEST (fixed) — `test_health_check` asserted `"mode" in data`
The `/health` endpoint was simplified to `{"status": "ok"}` when separate-mode scheduler was dropped.
Removed the stale assertion.

Suite **347 passed**; ruff clean on all changed files.

## Session 17 round-3 — cleanup pass (2026-09-08)

### DEAD CODE REMOVED — `DataProcessor.filter_data()` and `scheduler/engine.py::log_audit()`
`filter_data()` had zero production callers (filters are applied at the SQL layer via `connector.execute_query`);
its three test methods were removed alongside it. `log_audit()` was defined but never called — inline
`AuditLog(...)` writes in `runner.py` and `delivery/email.py` are the actual path. Both removed with no
regression risk.

### BUG (fixed) — query-builder adapter drops duplicate column names (`adapter.py`)
`execute_query` materialized rows via `dict(zip(names, row))` (SQLite/MySQL) or `dict(r)` (PostgreSQL),
which silently collapses duplicate keys (last value wins). A query like `SELECT a.id, b.id FROM a JOIN b`
would lose `a.id`. Fixed by adding `_resolve_column_names()` that suffixed duplicates as `name_2`, `name_3`,
… and applying it in all three connector paths. Regression: `tests/unit/test_query_adapter.py::test_execute_sqlite_duplicate_column_names`.
Suite **348**.

### REFACTOR — shared auth helpers (`app/routes/_auth_helpers.py`)
`get_role_value()`, `get_auth_source_value()`, and `check_role()` were duplicated across `admin.py`,
`designer.py`, `portal.py`, `settings.py`, `preview.py`, and `api/query_builder.py` (some with subtle
inconsistencies). Extracted into a single module with null-safe handling for both Enum members and raw
strings. All consumers updated to import from the shared module. Regression: `tests/unit/test_auth_helpers.py`
(12 cases covering None users, plain strings, enum members, and role-check logic). Suite **360**.

### DEV INFRA — local northwind test database
Added `docker-compose.test.yml` (spins up postgres:northwind on `localhost:5434`), `test-assets/northwind/init.sql`
(customers, employees, products, orders, order_details + `sales_by_region`, `employee_performance`,
`product_sales_summary` views), and `.env.test`. `tests/integration/test_real_data_export.py` now reads
`NORTHWIND_*` env vars with fallback to the remote anchor (`10.0.1.33`). Developers can run the full
stack locally without external dependencies: `docker compose -f docker-compose.yml -f docker-compose.test.yml up`.

Suite **357 passed**; ruff clean on all changed files.

## Session 17 round-4 — delivery config extraction (2026-09-08)

### REFACTOR — delivery config builder/redactor extracted (`app/services/delivery/config.py`)
`build_delivery_config()` and `redact_delivery_config()` were duplicated inline across admin.py's
create/update schedule endpoints (~50 lines of branching logic). Extracted into a single pure module
with full test coverage. Both endpoints now delegate to `build_delivery_config()`; read endpoints use
`redact_delivery_config()`. Regression: `tests/unit/test_delivery_config.py` (11 cases covering email/sftp/smb/webhook
types, nested redaction, edge cases). Suite **368**.

## Session 17 scan round-5 — optimizer + versioning (2026-09-08)

### BUG (fixed) — optimizer `group_by_no_agg` fired on empty select (`optimizer.py:67`)
The check used `not any(c.aggregation for c in config.select)` which is True when `config.select` is
empty, so a valid `SELECT * ... GROUP BY` query was incorrectly flagged. Now requires `config.select`
to be non-empty before checking for aggregates. Regression: `test_group_by_with_select_star_does_not_flag_no_agg`.

### BUG (fixed) — optimizer emitted duplicate suggestions for repeated WHERE columns (`optimizer.py:93-104`)
The WHERE clause loop did not deduplicate. Two filters on the same column produced two identical
"missing_index" suggestions. Now tracks `(code, table, column)` tuples and skips duplicates.
Regression: `test_duplicate_where_columns_are_deduplicated`.

### BUG (fixed) — optimizer crashed on raw-string join_type (`optimizer.py:85`)
`join.join_type.value` assumed the field was always a JoinType enum member. A manually-constructed
`JoinConfig` with a plain string bypassed Pydantic validation and raised `AttributeError`. Now uses
`hasattr(value, "value")` guard. Regression: `test_join_type_raw_string_does_not_crash`.

### IMPROVEMENT (fixed) — `restore_version()` now computes `diff_summary` (`restore.py`)
Previously restored versions had `diff_summary=None`, so users could not see what changed when
restoring. Now diffs the current definition against the restored one via `ReportDiffEngine` and stores
the result. The versioning service's `save_version()` already computed this for manual saves; restore
now behaves consistently.

Regression: 3 new optimizer tests in `tests/unit/test_query_optimizer.py`. Suite **371**.

## Session 17 round-6 — preview.py HTML builder extraction (2026-09-08)

### REFACTOR — extracted `_build_section_html()` and `_build_page_html()` (`app/routes/preview.py`)
The ~580-line `render_report_with_data()` function contained inline HTML assembly for section wrappers
and the full page template. Extracted these into two pure helpers:
- `_build_section_html(section_type, section_name, elements_html, hide_name)` — wraps elements in a
  section div with optional header
- `_build_page_html(title, description, sections_html)` — assembles the full HTML page template with
  placeholder replacement

Both helpers have no database state and are independently testable. Cuts ~100 lines from the main
function. Regression: `tests/unit/test_preview_helpers.py` (5 cases). Suite **376**.

## Session 17 round-7 — configurable preview row limits (2026-09-08)

### ENHANCEMENT — added `PREVIEW_TABLE_ROW_LIMIT` and `PREVIEW_CHART_ROW_LIMIT` to Settings (`app/config.py`)
preview.py previously hard-coded 50-row table limit and 10-row chart limit. Now reads these from
`settings.PREVIEW_TABLE_ROW_LIMIT` and `settings.PREVIEW_CHART_ROW_LIMIT` (defaults 50/10 match the
previous values). Operators can tune the caps for large datasets via environment variables without
code changes. Suite **376**.

## Session 17 round-8 — preview.py label helper extraction (2026-09-08)

### REFACTOR — extracted `_build_label_html()` (`app/routes/preview.py`)
Pure helper that builds the element label div with proper HTML escaping. Handles blank labels and
`hide_label=True`. Removed unused top-level `get_role_value` import. Regression: 4 new tests in
`tests/unit/test_preview_helpers.py`. Suite **380**.

## Session 17 round-9 — designer.py export helper extraction (2026-09-08)

### REFACTOR — extracted `_build_report_export_data()` (`app/routes/designer.py`)
Pure helper that builds the JSON export dict for a report definition. Route handler delegates to this
helper. Regression: 4 new tests in `tests/unit/test_designer_helpers.py`. Suite **384**.

## Session 17 round-10 — admin.py schedule form parsing helpers (2026-09-08)

### REFACTOR — extracted `_parse_json_body()`, `_validate_output_format()`, `_parse_optional_uuid()` (`app/routes/admin.py`)
Pure helpers for schedule form parsing:
- `_parse_json_body()`: merges JSON body into form fields, preferring JSON values
- `_validate_output_format()`: validates and normalizes output format (raises 400 on invalid)
- `_parse_optional_uuid()`: safely parses UUID strings (returns None on failure)

Both create_schedule and update_schedule endpoints now use these helpers, eliminating ~60 lines of
duplicated parsing/validation logic. Regression: `tests/unit/test_admin_helpers.py` (11 cases).
Suite **395**.

## Session 17 round-11 — designer.py report create/update helpers (2026-09-08)

### REFACTOR — extracted `_parse_definition_field()`, `_build_commit_message()` (`app/routes/designer.py`)
Pure helpers for report create/update:
- `_parse_definition_field()`: parses JSON definition field with fallback to default
- `_build_commit_message()`: formats version commit messages consistently

Both create_report and update_report endpoints now use these helpers, eliminating ~30 lines of
duplicated parsing/formatting logic. Regression: `tests/unit/test_designer_report_helpers.py` (10
cases). Suite **405**.

## Session 17 round-12 — database.py session close cleanup (2026-09-08)

### CLEANUP — removed redundant `session.close()` from `get_db()` (`app/database.py`)
The `async with async_session_factory() as session:` context manager already closes the session on
exit, so the explicit `await session.close()` in the `finally` block was unnecessary. Simplified to
just `yield session` inside the context manager. Suite **405**.

## Session 17 round-13 — PDF exporter table styling extraction (2026-09-08)

### REFACTOR — extracted pure helpers for table styling (`app/services/exporters/pdf.py`)
- `_build_table_style()`: builds a TableStyle with default + conditional formatting
- `_compute_conditional_formatting()`: pure helper that returns directives as `(command, start, end, value)` tuples
- `_apply_conditional_formatting()`: applies directives to a TableStyle

The pure helpers are independently testable without a story list or canvas. Reduces coupling between
table rendering and style computation. Regression: `tests/unit/test_pdf_exporter_helpers.py` (6 cases).
Suite **411**.

## Session 17 round-14 — report preview summary for schedule list (2026-09-08)

### FEATURE — added `generate_report_preview_summary()` (`app/services/reports/preview_summary.py`)
Extracts layout structure (section types, element counts) from a report definition. Returns a small
HTML snippet showing what each scheduled report will look like without running the full export. The
`list_schedules` API now includes a `preview_summary` field so the UI can display layout overviews
in the schedule list. Regression: `tests/unit/test_preview_summary.py` (4 cases). Suite **415**.

## Session 17 round-15 — scheduled-export failure retry (2026-09-08)

### FEATURE — automatic retry for transient failures (`app/services/scheduler/engine.py`)
When a scheduled report execution fails transiently (e.g., network blip, SMTP server temporarily
down), the scheduler now automatically retries once after 30 seconds before sending a failure
notification. Configurable via `max_retries` kwarg (default 1). Prevents transient delivery failures
from marking schedules as permanently failed. Regression: `tests/unit/test_scheduler_retry.py` (2
cases). Suite **417**.

## Delivery scan — SMB + webhook (Session 16 round-3, 2026-09-05)
`app/services/delivery/smb_webhook.py` was the one delivery module with **no** test coverage. Its send
functions wrap everything in `except Exception: log; return False`, so a failure anywhere (bad HMAC,
connection error, missing share) is indistinguishable from success at the call site — the worst failure
mode for delivery. Scanned it natively: `httpx` is installed (webhook path real); `smbclient`/`smbprotocol`
are not, so they are faked via `sys.modules` exactly as `test_delivery_sftp.py` does for `asyncssh`.

**Findings — all verified clean, no bugs:**
- Webhook HMAC signing is correct: `X-Webhook-Signature: t=<unix-seconds>,v1=<sha256hex(secret, body)>`,
  body is the JSON-encoded payload. An independently recomputed HMAC matched the emitted signature in the
  test, so signed-webhook deliveries will not be rejected by consumers for a bad signature. `secret=None`
  correctly omits the header; custom headers merge with the forced `Content-Type: application/json`.
- SMB UNC path construction is correct: `smb_url = \\server\share`, remote file = `\\server\share/<remote_path>/<file>`
  using the forward-slash remote form that `smbprotocol` expects. `register_session` is called with the
  flat `username`/`password` from the schedule config (matches how the connection form stores auth).
- Missing-dependency and validation guards return the expected `False` / `(False, msg)` tuples.

Regression coverage: `tests/unit/test_delivery_smb_webhook.py` (14 cases). Full suite **282 passed**;
`ruff check` clean on changed files. (The `\{` invalid-escape *warning* seen in an earlier ad-hoc run was
a shell-escaping artifact of the `-c` string — the actual source uses the valid `\\{share}` escape and
compiles warning-free.)

## Connector scan — core DB + CSV/Excel (Session 16 round-4, 2026-09-05)

Scanned `postgresql.py`, `mysql.py`, `sqlserver.py`, `odbc.py`, `csv_excel.py` against the shared
`DataConnector` contract. Driver paths (asyncpg/asyncmy/pymssql/aioodbc) need real servers, so those were
verified by review; CSV/Excel run on pandas and were tested natively.

**BUG (fixed) — CSV field explorer showed a literal table name `"csv"`.** `CSVConnector.get_schema` built its
single table as `{"name": config.get("file_name", "csv"), ...}`, but `file_name` is **not** in CSV's
`config_fields` (only `file_path` + `delimiter`), so the lookup always fell back to `"csv"` regardless of the
actual file. The Fields-tab explorer therefore labeled every CSV connection `csv` — indistinguishable and
inconsistent with `ExcelConnector.get_schema`, which correctly reports real sheet names. Fixed by deriving the
name from the path: `os.path.splitext(os.path.basename(file_path))[0] or "csv"`. Regression:
`test_csv_schema_uses_filename_as_table_name` asserts `"sales"` for `sales.csv`; `test_csv_schema_falls_back_for_empty_path`
covers the except branch; `test_excel_schema_reports_each_real_sheet` locks in multi-sheet naming.

**Verified clean:** per-connector `get_schema` grouping (ordered-by-table, consecutive-run append) is correct
in all four DB connectors; placeholder style matches each driver (pg `$N`, mysql `%s`, sqlserver `@schema`,
odbc positional); `execute_query` result→DataFrame construction (`columns = [desc[0] ...]`) is right for the
cursor APIs used.

**Logged, not fixed (consistent with existing latent finding):** every DB connector's parameter path passes a
positional `param_values` list/tuple to a *raw* query string with no `$N`/`%s` placeholders
(`asyncpg.fetch(query, *params)`, `conn.execute(query, *params)`, `pymssql cursor.execute(query, params)`,
`aioodbc cursor.execute(query, params)`). A non-empty `schedule.parameters` would crash that connector's
export. Unreachable today — no route/UI populates `parameters` — but the fix is connector-specific (placeholder
dialect differs), so defer until parameter support is actually added.

Regression coverage: `tests/unit/test_csv_excel_connectors.py` (+3). Full suite **285 passed**; `ruff check`
clean on changed files.

## PDF export scan — subreport mode parity (Session 16 round-5, 2026-09-05)

`app/services/exporters/pdf.py` is now unit-testable natively: **reportlab 4.2.2 is installed in `.venv`**
(so the "PDF = Docker-only" note in SCAN_PLAN §0 is outdated for export logic — only *real northwind data*
still needs Docker). Streams decode via Adobe-ASCII85 + inflate, so rendered text can be asserted in-test.

**BUG (fixed) — non-inline subreports were silently dropped from PDF.** `_render_subreport` handled only
`inline` (embed nested elements) and `drill_down` (placeholder paragraph). The designer UI offers a third
mode, **`page` ("Start on New Page")**, plus `detached`; both fell through the if/elif with **no output** — a
"Start on New Page" subreport vanished from the exported PDF with no indication. The HTML/CSV/Excel exporters
already render a placeholder for *any* non-inline mode (cd1acea), so PDF was inconsistent. Fixed by collapsing
the `elif render_mode == "drill_down"` branch into an `else` that emits an ASCII placeholder paragraph for every
non-inline mode: `Sub-report (<mode>) - content not embedded in PDF export`. Uses a hyphen, not an em dash, to
stay within Helvetica's character set (ReportLab raises / drops glyphs outside it). Regression:
`test_exporters.py::TestSubreportExport` (+3) — inline embeds nested content; `page`/`detached`/`drill_down`
each produce the exact placeholder string; end-to-end `page`-mode export yields valid `%PDF` bytes. Confirmed
the placeholder text is present in the decoded PDF stream (parens arrive escaped as `\(`, so match accordingly).

## Scheduler scan — unwired failure notification (Session 16 round-5, 2026-09-05)

Reviewed `scheduler/engine.py`, `runner.py` (`execute_report` / `run_scheduler` / `deliver_report` /
`cleanup_old_reports`), and `services/cleanup.py`. Reconciliation, one-shot skip, stale-job removal (UUID-shaped
only; `cleanup_` protected), delivery wiring, and owner attribution are all correct and covered by the existing
`scheduler` tests.

**FEATURE HOLE (fixed) — scheduled-report failures never notified anyone.** `cleanup.py::send_failure_notification()`
(the backlog #9 "email on schedule error" feature) had **zero call sites**: `_execute_report`'s `except` handler
only logged, so a raised export/render silently produced no failure email. Wired it into that handler:
`from app.services.cleanup import send_failure_notification; await send_failure_notification(schedule.name, str(exc))`,
imported **lazily** inside the handler so this module's load path stays decoupled from the aiosmtplib-dependent
delivery stack, and wrapped in its own `try/except` so a notification failure can't mask the original error.
`send_email` defaults recipients to `settings.SMTP_FROM` (non-empty default) and degrades gracefully when
aiosmtplib is absent. Regression: `test_scheduler.py::test_execute_report_sends_failure_notification_on_error`
(monkeypatches `execute_report` to raise; asserts `send_email` is called with schedule name + error in subject/body).

**Dead code noted:** `scheduler/engine.py::log_audit()` is defined but never called — inline `AuditLog` writes in
`runner.execute_report` are the actual path. Leave for a cleanup pass (not a bug).

Regression coverage: `test_exporters.py` (+3) + `test_scheduler.py` (+1). Full suite **289 passed**; `ruff check`
clean on all changed files. The 3 remaining `TRY004` in `query_builder/template_io.py` are pre-existing and
accepted (documented exception contracts, out of this scan's scope).

## AI client scan — reasoning-model + fenced-JSON handling (Session 16 round-6, 2026-09-05)

Tested against the live llama.cpp server at `http://10.0.1.37:8080/` running
`/opt/llama.cpp/Models/SmolLM3-3B-128K-Q4_K_M.gguf` (~3B reasoning model; slower + lower accuracy than prod, but
valid for smoke-testing the OpenAI-compatible path). `app/services/ai/client.py` is dependency-light (httpx +
stdlib) so it imports and runs natively.

**BUG (fixed) — `chat_completion` ignored reasoning-model output.** It read only `message.content`; reasoning
models (DeepSeek-R1, SmolLM3 reasoning, ...) return their output in `reasoning_content` and leave `content`
empty, so the client returned `""`. Fixed to fall back to `reasoning_content` when `content` is blank (prefers
`content` when both are present), covering both instruct and reasoning endpoints.

**BUG (fixed) — JSON generators choked on fenced/prose-wrapped JSON.** `AIReportGenerator.generate_report` and
`AILayoutAssistant.suggest_layout` did bare `json.loads(response)`. Local models routinely wrap JSON in
```` ```json ```` fences or surround it with prose, so parsing failed even though the model produced valid JSON
(verified live: the 3B model emitted a well-formed report definition inside ``` fences, which previously raised
`ValueError: Invalid report definition generated by AI`). Added module-level `_extract_json_response`: strips an
optional ```` ```lang ```` fence, then — as a fallback — extracts the outermost balanced `{...}`/`[...]` block.
Makes the JSON generators consistent with `AISQLGenerator.generate_sql`, which already stripped sql fences.

**Not a bug:** the tiny model sometimes returns prose instead of JSON when asked for raw JSON (model quality, not
a client defect). `_extract_json_response` correctly raises `JSONDecodeError` there, which the generators convert
to their documented `ValueError`.

Regression: `tests/unit/test_ai_client.py` (10) — uses a fake httpx module (`HTTPStatusError` stubbed) so the
suite needs no network. Full suite **299 passed**; `ruff check` clean on changed files.

## AI client retry + error classification (Session 16 round-6 follow-up, 2026-09-05)

Bad LLM responses are now accommodated two ways, so a flaky model or transient network hiccup no longer turns
into an opaque 500.

**Retry on transient failures.** `AIClient.chat_completion` wraps the request in a bounded retry loop. It retries
on `httpx.HTTPStatusError` with a transient status (`_TRANSIENT_STATUS = {408, 429, 500-599}`) and on transport
errors (`httpx.HTTPError` subclasses: `ConnectError`, `NetworkError`, `TimeoutException`, …), up to `retries`
(default 2) with exponential backoff (`retry_backoff * 2**attempt`, default 0.5s). Non-transient 4xx (auth, bad
request) raise immediately — repeating them only fails again. `_sleep_before_retry` logs each retry so flakiness
is visible in runner logs.

**Distinguish "retry now" from "notify + ask to retry".** Added `AILLMResponseError(ValueError)` for the case
where the model ran but returned unusable output (invalid JSON for a structured request). `classify_ai_error(exc,
operation)` — a fastapi-free helper living in the client module so all routes agree — maps:
`AILLMResponseError` → **502 Bad Gateway** (upstream gave an unusable response; user can retry); transient
transport/HTTP errors after the budget is exhausted → **503 Service Unavailable** (service flaky; retry later);
other → **500**. Report/layout generators now raise `AILLMResponseError` instead of bare `ValueError` so the
mapping is preserved end-to-end.

**Route wiring.** `app/routes/ai.py` gained `_ai_error_to_http()` delegating to `classify_ai_error`; all five
endpoints (`generate-report`, `generate-sql`, `suggest-layout`, `get_insights`, `chat`) now map failures through
it instead of a hardcoded 500. `app/routes/api/query_builder.py::nl_to_query` wraps its AI call the same way. This
gives the SPA a retryable status code rather than a sanitized generic 500.

Regression: `tests/unit/test_ai_client.py` (+7). Fake `httpx` module builds a proper exception hierarchy
(`ConnectError`/`NetworkError`/`TimeoutException` subclass `HTTPError`; `HTTPStatusError` carries `.response`) so
the client's `isinstance` checks and `e.response.status_code` access work; backoff is set to 0 in tests. Suite
**306 passed**; `ruff check` clean on changed files.

## API key scan — cleartext secret storage (Session 16 round-7, 2026-09-05)

`app/routes/api_keys.py` and `app/services/api_key.py` had **no** dedicated tests.

**BUG (fixed) — API keys stored in cleartext (CWE-312).** `APIKey.key` persisted the raw `ir_<token_urlsafe>`
token; anyone with DB read access (leaked backup, over-privileged role, SQLi) got every live API key. Fixed:
`generate_api_key` stores `sha256(key)` and returns the plaintext to the caller once; `validate_api_key` looks
up by hash. `hash_api_key()` helper added (unsalted SHA-256 is correct here — keys are ~256-bit entropy, we
never reverse them, rainbow tables are irrelevant). The `key` column is `String(64)`; a hex digest is exactly
64 chars, so no schema change.

**Migration `b2c3d4e5f6a7`.** Hashes existing rows in place via a bound sync connection (`op.get_conn()` +
`SELECT id,key` → `UPDATE ... sha256`). Downgrade deletes the rows (sha256 is one-way; keys must be
regenerated — acceptable, the dev DB is disposable). Alembic graph verified: chains onto `a1b2c3d4e5f6`; heads
are `b2c3d4e5f6a7` plus the pre-existing separate branch head `c7d8e9f0a1b2` (not created by this change).

**Latent bug hardened.** `validate_api_key` compared `api_key.expires_at < datetime.now(timezone.utc)`; drivers
that return naive datetimes (SQLite) raised `TypeError` on an expired key instead of rejecting it. Normalise
naive → UTC before comparing. Prod (asyncpg/timestamptz) returns aware datetimes, so this was a test-env-only
crash, but the fix removes the footgun.

**Feature hole (logged, not fixed):** `permissions` is decorative — `create_key` accepts a comma-joined
permissions list but no route enforces it, and `get_current_user_from_api_key` hardcodes `role="viewer"`
regardless of the key's permissions. Whether API keys should carry admin/execute rights is a product decision;
flagged rather than reimplemented authz here.

Regression: `tests/unit/test_api_key_service.py` (+6: hash-not-plaintext round-trip, correct/wrong/expired/
inactive rejection, last_used update) + `tests/unit/test_api_keys_route.py` (+1: dependency-override proving the
endpoint returns plaintext while the DB stores the hash). Suite **314 passed**; `ruff check` clean.

## Datasources scan — un-gated single-connection GET leaks credentials (Session 16 round-7b, 2026-09-05)

`app/routes/datasources.py` had no dedicated tests. Scanned all 10 endpoints (`list`, `list_connectors`,
`create`, `get`, `update`, `delete`, `test`, `schema`, `query`, `calculate`).

**BUG (fixed) — credential disclosure via un-gated GET /datasources/{id}.** `DataConnection.config` is a
JSONB blob holding connection credentials (DB passwords, hosts, etc.). `get_connection` returned that full
`config` to **any** authenticated user, while every other endpoint that touches connection config requires the
admin/designer role via `_require_designer`. So a plain authenticated user could read another connection's
secrets. Fixed by gating `get_connection` with `_require_designer` for parity. `list_connections` only returns
metadata (id/name/connector_type/created_at — no config) and correctly stays open to any authed user;
`list_connectors` returns connector *schema* (field definitions), not secrets.

**Verified clean:** `test_query`/`test_connection`/`get_schema`/`calculate` all call `_require_designer`;
`calculate_field` uses the now-sandboxed AST-based `CalculatedFieldEvaluator` (round-6). No SQLi in `list`
sorting (order_by on Column objects, not raw strings). `get_schema` still returns a raw 500 with `str(e)` —
minor internal-detail leak, left as a low-priority note.

Regression: `tests/integration/test_datasources.py` (+3: 401 unauth, 403 viewer with no config leaked, 200
designer). Confirmed the 403 test fails (returns 200) without the fix. Suite **317 passed**; `ruff check` clean.

## Versions scan — version endpoints ignore report ownership (Session 16 round-7c, 2026-09-05)

`app/routes/versions.py` (mounted at `/designer/reports/*`) had no dedicated tests. Scanned all 9 endpoints
(`list_versions`, `get_version_detail`, `create_version`, `restore`, `tag`, `untag`, `list_comments`,
`add_comment`, `diff`).

**BUG (fixed) — version endpoints did not enforce report ownership.** Every endpoint gated on *authentication*
(401 if no user) and the mutation ones additionally required admin/designer role, but none checked whether the
user was allowed to touch that report. So any logged-in user could read or mutate another team's version
history and past `definition` JSON. This contradicts the model documented in `designer.list_reports`: admins may
touch any report, other roles are scoped to reports they created ("lower-privilege users never leak other
teams' reports into their view").

Fixed by adding `_require_report_access(db, report_id, current_user)` (404 if missing, 403 if non-admin and not
creator) and calling it in all nine endpoints. Comment deletion stays owner-scoped in the service
(`delete_comment` already filters `created_by == user_id`).

**Verified clean:** `get_current_user_optional` returns None for unauth (handled by the 401 guards); role checks
on mutations are preserved above the ownership check.

Regression: `tests/integration/test_versions_auth.py` (+5: non-owner designer 403 on read versions/detail and
mutate (restore/tag), owner can read own (200), admin can read any (200)). Confirmed the 403 tests fail without
the fix. Suite **322 passed**; `ruff check` clean.

## Admin scan — schedule read endpoints leak delivery secrets (Session 16 round-7d, 2026-09-06)

`app/routes/admin.py` (mounted `/admin/*`) is the largest route module (~787L) and handles user + schedule +
audit management. Scanned the schedule read paths.

**BUG (fixed) — schedule list/detail leaked delivery credentials.** `list_schedules` (`GET /admin/api/schedules`)
and `get_schedule_api` (`GET /admin/api/schedules/{id}`) serialized `delivery_config` verbatim. For SFTP/SMB
deliveries that dict holds the plaintext `password`; for webhook deliveries it holds `secret`. Both endpoints are
gated only to admin/designer, so any such user could enumerate every schedule and read those secrets — a
credential-disclosure parallel to the round-7b datasources GET leak.

Fixed by adding `redact_delivery_config()` (recursively masks `password`/`secret` keys, leaves everything else)
and applying it to both read endpoints. Create/update flows still accept and store secrets; only responses are
redacted. Email configs carry no secret so they pass through unchanged.

**Verified clean:** `get_current_user_optional` returns None for unauth (handled by the 401/403 guards); role
checks preserved. No existing test asserted on `delivery_config` contents, so redaction is backward compatible.

Regression: `tests/integration/test_schedule_secret_leak.py` (+1: designer enumerates schedules + reads one;
asserts plaintext SFTP password and webhook secret absent, replaced with `***REDACTED***`, host/url retained).
Suite **323 passed**; `ruff check` clean.

## Auth scan — forgeable admin JWTs via placeholder SECRET_KEY (Session 16 round-8, 2026-09-06)

`app/config.py` `Settings.SECRET_KEY` defaulted to `"change-me-in-production"` with **no guard**; `app/auth.py`
signs JWTs with it (HS256). Anyone can forge an admin token → full auth bypass. **Proved:** a token created with the
default secret decodes via `decode_access_token()` and yields `sub=admin@example.com`.

**BUG (fixed):** added a pydantic `@field_validator("SECRET_KEY")` that raises at startup if the value is still the
placeholder, forcing operators to set a real secret (fail fast rather than ship weak). `tests/conftest.py` now sets a
real `SECRET_KEY` via `os.environ.setdefault(...)` *before* any `app.*` import, so `Settings()` (instantiated at
`app.config` import) validates in tests.

**Verified clean:** `decode_access_token` restricts `algorithms=[ALGORITHM]` (no algo-confusion); jose auto-rejects
expired tokens (`exp` is set by `create_access_token`); `authenticate_user` checks `auth_source == LOCAL` and bcrypt
`verify_password`; LDAP path re-binds as the user before granting access.

Regression: `tests/unit/test_auth.py` (+2: placeholder rejected, real key accepted). Suite **327 passed**; `ruff
check` clean.

## Settings scan — admin-gated, clean; in-memory config gap (Session 16 round-8d, 2026-09-06)

`app/routes/settings.py` (SMTP/LDAP/AI config). All three endpoints (`admin_settings`,
`update_settings`, `test-email`) are gated to `role == "admin"` via `get_role_value`; no bypass. Template renders
values with Jinja autoescape (no `| safe`), so no stored XSS; `smtp_password` is not echoed back (only `ai_api_key`
is, into an admin-only password field). **No high-severity security bug.**

**Functional gap (not fixed — larger change):** `update_settings` mutates the in-memory pydantic `Settings` singleton
with no persistence (no DB row / config rewrite). Admin UI changes are lost on restart and never propagate to a
separate runner process (docker-compose designer+runner), so runner-mode schedules read stale env-loaded settings.
Fixing needs a settings table + reload hook; logged as a follow-up, not a scan fix. Minor notes: `test-email` defaults
`to_email` to the public `test@example.com`; no field validation on `update_settings`.

Regression: none (clean). Suite 330 passed; ruff clean.

## Query-builder scan — unquoted identifiers allow SQLi (Session 16 round-8e, 2026-09-06)

`app/services/query_builder/config.py` + `app/routes/api/query_builder.py::/test`. The visual query builder builds a
`QueryConfig` client-side and executes it via `/test` against a live DB connection. WHERE *values* were safely quoted
(round-8), but table/column names, aliases, join conditions, ORDER BY direction and GROUP BY items are interpolated
**unquoted** into the generated SQL. A crafted config could inject SQL (e.g. an alias of `1; DROP TABLE orders`).

**BUG (fixed):** added `_validate_identifier()` (dotted alnum/underscore identifiers only; empty allowed for parser
back-compat) and wired it into `SelectColumn` (table/column/alias), `JoinConfig` (all 5 fields), `WhereFilter.field`,
`OrderByField.field` + restricted `direction` to ASC/DESC, and `QueryConfig.from_tables`/`group_by`. Rejection happens
at the pydantic model boundary, so `/test`, `/validate`, `/generate-sql` and `/save` all block malformed configs before
any query runs. Existing generator/adapter/optimizer/parser tests still pass.

Regression: `test_query_builder_sqli.py` (+9): malicious identifiers raise `ValidationError` at construction and are
rejected by `/test` with 422; verified the endpoint test fails (config reaches execution → 404 on bogus connection)
without the guard. Suite **343 passed**; ruff clean.

## Preview scan — unescaped data in HTML preview (Session 16 round-8f, 2026-09-06)

`app/routes/preview.py::render_report_with_data`. Table cell values, text content,
section/element labels and chart slice/bar labels were interpolated into the
preview HTML with raw f-strings and no escaping. A report pulling untrusted rows
(e.g. a free-text column) emits markup verbatim, so an embedded `<script>` reaches
the designer's browser on preview — stored XSS.

**BUG (fixed):** escape all data-derived interpolations with `html.escape()`
(`quote=True` on cell values/text to keep attribute contexts safe); numeric format
specs (`{value:,.2f}`) are unaffected. The `render()` dict path is unchanged.
Existing header/cell-mapping tests still pass.

Regression: `test_preview_rendering.py::test_preview_escapes_untrusted_cell_values`
(+1): asserts a `<script>` cell value renders as `&lt;script&gt;`, never raw;
verified the test fails without the escape. Suite **344 passed**; ruff clean.

## Export scan — unescaped cells in HTML export (Session 16 round-8g, 2026-09-07)

`app/services/exporters/excel_csv_html.py::HTMLExporter._render_table_html`. Table
cell values were appended into `<td>` with a raw f-string and no escaping — the
export counterpart of round-8f. Downloaded `.html` reports containing untrusted
rows would execute embedded markup when opened in a browser (stored XSS).

**BUG (fixed):** escape cell values and data-derived headers with
`html.escape(str(...), quote=True)`. Trusted conditional-formatting style
attributes are left intact (they come from `ConditionalFormatter`, not data).
Existing exporter tests still pass.

Regression: `test_exporters.py::test_table_cell_values_are_escaped` (+1): asserts
a `<script>` cell renders as `&lt;script&gt;`, never raw; verified the test fails
without the escape. Suite **345 passed**; ruff clean.

## Auth scan — LDAP search filter injection (Session 16 round-8h, 2026-09-07)

`app/auth.py::authenticate_ldap_user`. The login email was embedded directly into
an LDAP search filter with no escaping (LDAP injection, CWE-546). An email like
`*)(uid=)(*` could restructure the filter, enabling user enumeration or result
manipulation against the directory. Impact is bounded by the follow-up password
bind (the target user's DN must still authenticate), but enumeration is a real
info leak.

**BUG (fixed):** added `escape_ldap_filter()` (RFC 5012 escaping of `\ * ( )`)
applied to the login email before it enters the search filter. Local auth and JWT
paths unaffected.

Regression: `test_auth.py::TestLdapInjectionGuard` (+2): helper escapes
`* ( ) \`; call-time test verifies the constructed filter is escaped via a
monkeypatched `ldap3` (injected through `sys.modules` since the function does an
internal `import ldap3`). Verified the call-time test fails without the escape.
Suite **345 passed**; ruff clean.

## Preview scan — temp-preview endpoint lacks role gate (Session 16 round-8b, 2026-09-06)

`app/routes/preview.py`. `preview_report` correctly scopes (admin/designer any report; other roles only their own),
but `GET /preview/temp` — the designer "Preview" button path (`editor.html::previewReport`) — only required
authentication. It renders a client-supplied definition and executes each table element's `query` against an
application data-source connection (`resolve_data_source_connector`, which falls back to the first PostgreSQL
connection). So any logged-in user, including low-privilege viewers, could submit a definition carrying arbitrary SQL
against configured databases.

**BUG (fixed):** added the same admin/designer role gate used by `preview_report`. **Verified clean:**
`resolve_data_source_connector` pulls config from the stored `DataConnection` row (not client-supplied creds), so the
client can't inject credentials — only reference an existing connection ID or hit the fallback. The remaining concern
is that even designers can target any connection via the fallback / an enumerated `connection_id`; noted as a
lower-priority follow-up (scoped connection binding would be cleaner than a role gate alone).

Regression: `tests/integration/test_preview_auth.py` (+2: viewer 403 on a table-query definition; designer still 200).
Verified the 403 assertion fails without the fix (returned 200). Suite **330 passed**; `ruff check` clean.

## Query-builder template scan — IDOR on template read/delete (Session 16 round-8d, 2026-09-06)

`app/routes/api/query_builder.py`. Template `list`/`get`/`export`/`delete` operated on any template by ID with no
ownership check, so any authenticated user could read other users' saved query templates (their `query_config` holds
raw SQL) or delete them. Templates carry a `created_by` but were never scoped to their creator.

**BUG (fixed):** added `_require_template_access()` (admin oversees all; other roles only their own) and wired it into
list/get/export/delete — matching the report/version scoping from rounds 7c/7e/8c. Import now sets `created_by=importer`
so imported templates are owned (previously ownerless). Existing import/export tests still pass (they run as admin).

Regression: `test_query_builder_auth.py` (+4): non-owner designer gets 403 on read/export/delete of another's template
and is scoped out of the list; owner and admin retain access. Verified the 403/scope assertions fail without the fix.
Suite **334 passed**; ruff clean.

## Designer scan — landing page leaks all reports to designers (Session 16 round-8c, 2026-09-06)

`app/routes/designer.py`. `list_reports` correctly scopes non-admins to reports they created (documented: "designers
only see reports they created ... never leak other teams' reports"). But `designer_index` (GET `/designer/`, the
landing page) listed **all** reports to any admin/designer with no creator scoping — contradicting `list_reports` and
exposing every report's name/ID to any designer.

**BUG (fixed):** aligned `designer_index` with `list_reports` — admins see all; other roles scoped to
`created_by == current_user.id`. Regression: `test_api.py::test_designer_index_scopes_to_own_reports` (+1). Verified it
fails without the fix. Suite **328 passed**; ruff clean.

## Portal scan — per-report endpoints are IDORs (Session 16 round-7e, 2026-09-06)

`app/routes/portal.py` (end-user report portal). `portal_index` correctly scopes
non-admins to "outputs they generated or reports they own schedules for" (mirrors
`designer.list_reports`), but the per-report endpoints only required authentication.

**BUG (fixed) — IDOR on per-report portal endpoints.** `view_report_output`,
`download_report_output`, `export_report` and `get_report_parameters` all took
`{report_id}`/`{output_id}` path params and returned 404 only when the object did
not exist — no ownership check. So any logged-in user could view/download/export
another user's report output and read its parameters by directly requesting the
IDs. `export_report` was especially bad: it renders a saved report against **live
data** and streams the bytes to anyone who knows the ID.

Fixed by adding `_can_access_report()` (admin OR owns a schedule for the report)
and `_can_access_output()` (admin OR generated_by self OR owns a schedule for the
report) helpers, mirroring `portal_index`, and wiring them into all four
endpoints (403 on failure). Existing portal tests still pass; no over-blocking of
admins or legitimate owners.

Regression: `tests/integration/test_portal.py` (+2): non-owner designer gets 403
on all four per-report endpoints while admin can still download raw bytes and an
owner can read their own report's parameters. Verified the 403 assertions fail
without the fix (returned 200). Suite **325 passed**; `ruff check` clean.
