# InstantReports

A Python-based report design, scheduling, and delivery platform that replaces Crystal Reports/Jasper Reports. Features a web-based designer, scheduled report execution, multiple delivery methods (email, SFTP, SMB, webhook), and AI-assisted report creation.

> **⚠️ Work in Progress:** This project is still being actively developed and is only about two-thirds complete. It functions at this point in time, but there are still major bugs and missing or confusing features. Expect breaking changes as we move forward.

## Features

- **Report Designer** — Section-based layout builder with drag-and-drop, live preview, version history
- **Data Connectors** — PostgreSQL, MySQL, SQL Server, ODBC, CSV, Excel, REST API, GraphQL
- **Export Formats** — PDF, Excel, CSV, HTML with page numbering and conditional formatting
- **Scheduler** — Cron-based report execution with timezone support
- **Delivery Methods** — Email, SFTP, SMB/UNC paths, webhook with HMAC signing
- **AI Integration** — Natural language to report, SQL generation, layout suggestions (llama.cpp default)
- **Version Control** — Custom RCS with semantic diff, tags, comments, restore
- **Authentication** — Local accounts + LDAP, API key authentication
- **Web Portal** — End-user report viewer with search and filtering

## Quick Start

### Prerequisites

- Docker and Docker Compose (v2+)
- Python 3.12+ (for local development)

### Run with Docker

```bash
# Clone the repository
git clone https://github.com/Nurb4000/InstantReports.git InstantReports
cd InstantReports

# Start all services (PostgreSQL, InstantReports)
docker compose up --build

# Access the application
# Designer: http://localhost:8080
# PostgreSQL: localhost:5433 (internal: 5432)
```

### Initial Admin User

There is **no well-known default admin password**. The initial admin is created
either by the app on first run or manually via the seed script — in both cases with
a strong password you control.

**Option A — set a strong password and let the app seed it on startup:**

```bash
export INITIAL_ADMIN_EMAIL=admin@example.com
export INITIAL_ADMIN_PASSWORD='a-strong-password-of-at-least-8-chars'
```

Then start the app (`uvicorn app.main:app`). It seeds the admin on first run and
refuses to do so with a password that is too short (< 8 chars) or a known placeholder
(e.g. `admin`).

**Option B — seed manually with a random strong password:**

```bash
python scripts/seed_admin.py
```

With `INITIAL_ADMIN_PASSWORD` unset, the script generates a random strong password,
prints it once, and will not show it again — save it. A provided password that is too
short or a known placeholder is rejected instead of being seeded.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ir:secret@postgres:5432/instantreports` | PostgreSQL connection string |
| `SECRET_KEY` | (required) | JWT secret key — must be set to a strong value |
| `LDAP_URL` | (empty) | LDAP server URL (e.g., `ldap://localhost:389`) |
| `LDAP_BIND_DN` | (empty) | LDAP bind DN |
| `LDAP_BIND_PASSWORD` | (empty) | LDAP bind password |
| `LDAP_SEARCH_BASE` | (empty) | LDAP search base DN |
| `AI_BASE_URL` | `http://llama-cpp:8080/v1` | AI backend URL (OpenAI-compatible) |
| `AI_API_KEY` | `none` | AI API key (leave as `none` for llama.cpp) |
| `AI_MODEL` | `local-model` | AI model name |
| `AI_ENABLED` | `false` | Enable AI features |
| `SMTP_HOST` | (empty) | SMTP server for email delivery |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | (empty) | SMTP username |
| `SMTP_PASSWORD` | (empty) | SMTP password |
| `SMTP_FROM` | `reports@example.com` | Sender email address |
| `REPORT_RETENTION_DAYS` | `90` | Days to keep report outputs before cleanup |

### Using `.env` File

Copy `.env.example` to `.env` and adjust values:

```bash
cp .env.example .env
# Edit .env with your settings
```

## Architecture

The application runs as a single container with the scheduler embedded. The designer mode provides
the web-based report designer UI, data source management, admin features, and scheduled report
execution — all in one process.

**Access:** `http://localhost:8080`

## Project Structure

```
InstantReports/
├── app/                    # Application code
│   ├── main.py            # FastAPI app with embedded scheduler
│   ├── runner.py          # Scheduler entry point
│   ├── config.py          # Pydantic settings
│   ├── database.py        # SQLAlchemy async engine
│   ├── auth.py            # Authentication (local + LDAP)
│   ├── models/            # Database models
│   ├── routes/            # API routes
│   ├── services/          # Business logic
│   │   ├── connectors/    # Data source connectors
│   │   ├── engine/        # Report rendering engine
│   │   ├── exporters/     # PDF, Excel, CSV, HTML exporters
│   │   ├── scheduler/     # APScheduler integration
│   │   ├── delivery/      # Email, SFTP, SMB, webhook delivery
│   │   ├── ai/            # AI integration (llama.cpp/OpenAI)
│   │   ├── versioning/    # Custom RCS with semantic diff
│   │   └── api_key.py     # API key management
│   └── templates/         # Jinja2 HTML templates
├── static/                # Static files (JS, CSS, images)
├── alembic/               # Database migrations
├── scripts/               # Utility scripts
│   └── seed_admin.py      # Create initial admin user
├── test-assets/           # Sample reports
│   └── sample_reports/    # Demo .ir.json report definitions
└── docs/                  # Documentation
```

## Data Connectors

InstantReports supports multiple data source types:

| Type | Library | Description |
|---|---|---|
| PostgreSQL | asyncpg | Direct async connection |
| MySQL/MariaDB | asyncmy | Direct async connection |
| SQL Server | pymssql | Native Python driver |
| ODBC | aioodbc | Generic ODBC (DSN-based) |
| CSV | pandas | File upload or path |
| Excel | openpyxl | .xlsx/.xls files |
| REST API | httpx | JSON API endpoints |
| GraphQL | httpx + gql | GraphQL queries |

## Report Definition Format

Reports are stored as JSON documents in PostgreSQL. Example structure:

```json
{
  "name": "Monthly Sales Report",
  "description": "Sales breakdown by region",
  "data_sources": [
    {
      "id": "ds1",
      "name": "Sales DB",
      "connector_type": "postgresql",
      "query": "SELECT * FROM sales WHERE month = $month"
    }
  ],
  "parameters": [
    { "name": "month", "type": "date", "required": true }
  ],
  "layout": {
    "page": { "size": "A4", "orientation": "portrait" },
    "sections": [
      {
        "type": "header",
        "elements": [
          { "type": "text", "content": "Monthly Sales — {{month}}" }
        ]
      },
      {
        "type": "detail",
        "data_source": "ds1",
        "group_by": "region",
        "elements": [
          {
            "type": "table",
            "columns": [
              { "field": "product", "header": "Product" },
              { "field": "revenue", "header": "Revenue", "format": "$#,##0.00" }
            ]
          }
        ]
      }
    ]
  }
}
```

## AI Integration

InstantReports includes AI features powered by llama.cpp (default) or OpenAI:

### Supported AI Features

- **Natural Language to Report** — Describe what you want, get a report definition
- **SQL Generation** — Write queries in plain English
- **Layout Suggestions** — AI recommends optimal report layouts
- **Data Insights** — Automatic analysis and summaries

### Configuration

```bash
# Using llama.cpp (default)
AI_BASE_URL=http://localhost:8080/v1
AI_API_KEY=none
AI_MODEL=your-model-name
AI_ENABLED=true

# Using OpenAI
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-your-key
AI_MODEL=gpt-4o
AI_ENABLED=true
```

## Delivery Methods

Reports can be delivered via:

| Method | Description | Use Case |
|---|---|---|
| **Email** | SMTP with attachments | Standard delivery |
| **SFTP** | SSH File Transfer Protocol | Remote server delivery |
| **SMB/UNC** | Windows file shares | Network drive drops |
| **Webhook** | HTTP POST with HMAC signing | API integrations |

## Version Control

Reports include a custom version control system (no external dependencies):

- **Semantic Diff** — Understands report structure, not just JSON text
- **Tags** — Mark important versions (e.g., "approved", "v2.1")
- **Comments** — Collaborative discussion on versions
- **Restore** — Revert to any previous version
- **History** — Full audit trail of all changes

## Exporting and Importing Reports

Reports can be exported as JSON files and imported into other InstantReports instances. This is useful for:
- Sharing report templates between team members
- Backing up report definitions
- Migrating reports between environments (dev → staging → prod)

### What Gets Exported

When you export a report, the following is included:
- ✅ Report definition (layout, sections, elements, queries)
- ✅ Connection metadata (host, port, database, username, connector type)
- ✅ Parameters and calculated fields
- ❌ **Credentials** (passwords, API keys, secrets) — for security

### Importing Reports

When importing a report:
1. The system auto-creates data connections from the exported templates
2. You must manually enter passwords/secrets when prompted
3. If a connection with the same name already exists, it will be reused

**Note:** Sample reports reference the northwind database which is now created automatically on the main postgres instance (port 5433). No additional setup required.

### Security Note

**Credentials are never exported.** This is intentional:
- Exported files can be safely shared via email, git, etc.
- Importing on another system requires re-entering sensitive credentials
- Connection templates include enough metadata to auto-create connections, but not enough to access them

## API Authentication

### Session-Based Auth

Used by the web UI. Login via `/auth/login` returns a JWT stored in an HTTP-only cookie.

### API Key Auth

For external integrations. Generate keys via the admin UI or API:

```bash
# Generate API key
curl -X POST "http://localhost:8000/api-keys/?name=MyApp&permissions=read,execute" \
  -H "Authorization: Bearer <your-jwt>"

# Use API key
curl -H "X-API-Key: ir_..." http://localhost:8000/portal/reports
```

## Scheduler

Reports can be scheduled using cron expressions:

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6) (Sunday to Saturday)
│ │ │ │ │
* * * * *
```

Examples:
- `0 8 * * *` — Daily at 8 AM
- `0 9 * * 1-5` — Weekdays at 9 AM
- `0 0 1 * *` — First day of each month
- `*/5 * * * *` — Every 5 minutes

## Report Retention

Generated reports are stored as BLOBs in PostgreSQL. Configure retention:

```bash
REPORT_RETENTION_DAYS=90  # Delete outputs older than 90 days
```

A cleanup job runs daily at 2 AM (configurable in `app/runner.py`).

## Development

### Local Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up PostgreSQL
createdb instantreports
psql instantreports -c "CREATE USER ir WITH PASSWORD 'secret';"
psql instantreports -c "GRANT ALL PRIVILEGES ON DATABASE instantreports TO ir;"

# Run migrations
alembic upgrade head

# Seed admin user
python scripts/seed_admin.py

# Start the application (scheduler runs embedded)
uvicorn app.main:app --reload
```

### Running Tests

The test suite runs against **PostgreSQL** (the same backend as production) via the
env-overridable `TEST_DATABASE_URL` in `app/config.py`, which defaults to
`postgresql+asyncpg://ir:secret@localhost:5432/instantreports`. `SECRET_KEY` is set
automatically by `tests/conftest.py`, so no `.env` is needed for tests.

```bash
# Option A — spin up a throwaway Postgres container (recommended)
docker run -d --name ir-test-pg \
  -e POSTGRES_DB=instantreports -e POSTGRES_USER=ir -e POSTGRES_PASSWORD=secret \
  -p 5432:5432 postgres:16-alpine

# Option B — use a local Postgres instance (createdb + CREATE USER ir …)

# Run the suite (see findings/InstantReports-security-review.md for the
# 2 known pre-existing failures and the remediation tracker)
pytest
```

For full setup options, live e2e instructions, and simulating the AI endpoint, see the
"PostgreSQL test setup (backend + e2e)" section of
`findings/InstantReports-security-review.md`.

## Troubleshooting

### Cannot login with default credentials

For security, the app no longer seeds a well-known default admin password (see
`scripts/seed_admin.py`). To create the initial admin, set a strong password and
run the seed script:

```bash
export INITIAL_ADMIN_EMAIL=admin@example.com
export INITIAL_ADMIN_PASSWORD='a-strong-password-of-at-least-8-chars'
python scripts/seed_admin.py
```

If `INITIAL_ADMIN_PASSWORD` is left unset, the script generates a random strong
password and prints it once — save it, as it will not be shown again.

### AI features not working

Enable AI and configure the backend:

```bash
AI_ENABLED=true
AI_BASE_URL=http://your-llama-server:8080/v1
AI_API_KEY=none  # For llama.cpp
```

### PDF export fails

Ensure WeasyPrint system dependencies are installed (handled in Dockerfile). For local dev:

```bash
# Ubuntu/Debian
sudo apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2

# macOS
brew install pango cairo gdk-pixbuf
```

## License

Distributed under the terms of the Apache License 2.0. This project is open source and available for use and modification. See LICENSE for details.

## Support

For issues and questions, please open a new issue in this repository on GitHub.
