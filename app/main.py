from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings as app_settings
from app.database import get_db
from app.models import *

logger = logging.getLogger(__name__)


def _warn_connection_encryption_key(logger: logging.Logger) -> bool:
    """Warn once at boot if connection credentials depend on ``SECRET_KEY`` (M6).

    Returns True when the warning was emitted. When no dedicated
    ``CONNECTION_SECRET_ENCRYPTION_KEY`` is configured, stored credentials are
    encrypted with a key derived from ``SECRET_KEY``; rotating ``SECRET_KEY`` then
    makes every stored connection credential undecryptable with no runtime
    diagnostic (scheduled reports / webhooks silently start failing auth). The
    warning nudges operators to pin a stable dedicated key and rotate it
    deliberately alongside a re-encryption pass.
    """
    if app_settings.CONNECTION_SECRET_ENCRYPTION_KEY:
        return False
    logger.warning(
        "CONNECTION_SECRET_ENCRYPTION_KEY is not set; connection credentials are "
        "encrypted with a key derived from SECRET_KEY. Rotating SECRET_KEY will make "
        "stored credentials unreadable -- set a stable CONNECTION_SECRET_ENCRYPTION_KEY "
        "and rotate it deliberately (re-encrypting stored secrets) instead. Use "
        "app.services.connection_encryption.encrypt_config_with_key / "
        "decrypt_config_with_key for a rotation pass."
    )
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-create tables and seed admin user on startup (dev convenience)
    try:
        engine = create_async_engine(app_settings.DATABASE_URL)
        async with engine.begin():
            # Rely on Alembic migrations; create_all may diverge (skip)
            pass
        await engine.dispose()
        logger.info("Database tables ensured (created if missing)")

        # M6: surface the connection-credential key-rotation hazard at boot so it
        # is not a silent, operational failure.
        _warn_connection_encryption_key(logger)

        # Seed admin user and default northwind connection if not exists
        import uuid

        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from app.auth import hash_password
        from app.database import async_session_factory
        from app.models.connection import DataConnection
        from app.models.user import AuthSource, User, UserRole

        async with async_session_factory() as db:
            # Load admin-edited settings persisted in the DB
            try:
                from app.services.settings_store import load_persisted_settings

                loaded = await load_persisted_settings(db)
                if loaded:
                    logger.info(f"Loaded {loaded} persisted setting override(s)")
            except Exception as e:
                logger.warning(f"Could not load persisted settings: {e}")

            # Seed admin user
            result = await db.execute(select(User).where(User.email == "admin@example.com"))
            if not result.scalar_one_or_none():
                import os

                env_email = os.getenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
                env_pass = os.getenv("INITIAL_ADMIN_PASSWORD")
                if not env_pass or len(env_pass) < 8 or env_pass in {
                    "admin",
                    "password",
                    "12345678",
                }:
                    # Do not seed a known-weak default admin. Require env.
                    logger.warning(
                        "Refusing to seed weak default admin; set "
                        "INITIAL_ADMIN_EMAIL/INITIAL_ADMIN_PASSWORD with a strong "
                        "password (>=8 chars)."
                    )
                else:
                    admin = User(
                        email=env_email,
                        name="Admin",
                        password_hash=hash_password(env_pass),
                        role=UserRole.ADMIN,
                        auth_source=AuthSource.LOCAL,
                        is_active=True,
                    )
                    db.add(admin)
                    try:
                        await db.commit()
                        logger.info(f"Seeded initial admin user ({env_email})")
                    except IntegrityError as e:
                        await db.rollback()
                        logger.info(f"Admin user may already exist: {e}")

            # Seed default northwind connection with fixed UUID for sample reports
            nw_result = await db.execute(
                select(DataConnection).where(DataConnection.name == "Northwind (local)")
            )
            if not nw_result.scalar_one_or_none():
                # Get the admin user ID for created_by
                admin_result = await db.execute(select(User).where(User.email == "admin@example.com"))
                admin_user = admin_result.scalar_one()
                
                # Use a fixed UUID so sample reports can reference it
                northwind_conn_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
                northwind_conn = DataConnection(
                    id=northwind_conn_id,
                    name="Northwind (local)",
                    connector_type="postgresql",
                    config={
                        "host": "postgres",
                        "port": 5432,
                        "database": "northwind",
                        "user": "northwind",
                        "password": "northwind",
                    },
                    created_by=admin_user.id,
                )
                db.add(northwind_conn)
                try:
                    await db.commit()
                    logger.info("Seeded default northwind connection (localhost:5434)")
                except IntegrityError as e:
                    await db.rollback()
                    logger.info(f"Connection may already exist: {e}")
    except Exception as e:
        logger.warning(f"Could not auto-seed database: {e}")

    # Start the embedded scheduler as a background task. Storing it on app.state
    # lets us cancel it cleanly on shutdown instead of relying on the deprecated
    # ``@app.on_event("startup")`` which discards the task handle.
    import asyncio

    from app.runner import run_scheduler

    app.state._scheduler_task = asyncio.create_task(run_scheduler())
    logger.info("Embedded scheduler started")

    yield

    # Graceful shutdown: cancel the scheduler and wait for it to finish.
    if hasattr(app.state, "_scheduler_task"):
        app.state._scheduler_task.cancel()
        try:
            await app.state._scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("Embedded scheduler stopped")


app = FastAPI(
    title="InstantReports",
    description="Report design, scheduling, and delivery platform",
    version="0.1.0",
    lifespan=lifespan,
    debug=app_settings.DEBUG,
)

_cors_origins = [
    origin.strip()
    for origin in app_settings.CORS_ALLOW_ORIGINS.split(",")
    if origin.strip()
]

if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Optional HTTP -> HTTPS redirect (outermost so it runs before anything else).
# Gated by HTTPS_REDIRECT; safe to leave off for local HTTP dev and tests.
from app.middleware.https_redirect import HTTPSRedirectMiddleware  # noqa: E402

app.add_middleware(HTTPSRedirectMiddleware)

# Defense-in-depth response headers (CSP + nosniff). Registered before CSRF so
# it is the outermost application middleware and wraps the full cycle -- every
# response, including CSRF-rejected 403s, carries the headers. See
# app/middleware/security_headers.py.
from app.middleware.security_headers import SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)

# CSRF protection for cookie-authenticated, state-changing web requests. Added
# after CORS so it wraps the full request/response cycle. See app/middleware/csrf.py.
from app.middleware.csrf import CsrfProtectionMiddleware  # noqa: E402

app.add_middleware(CsrfProtectionMiddleware)

# Custom exception handlers. Every error returns a consistent JSON payload:
#   {"detail": "<user-facing message>", "status_code": <int>}
# so the client-side toast can surface meaningful messages instead of a generic
# "An error occurred". 4xx client errors carry app-controlled, non-sensitive
# detail (validation/auth/not-found), so it is always surfaced. 5xx may embed
# internals (str(exc) from a caught exception), so those are hidden in
# production and only shown when debug mode is enabled.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    status_code = exc.status_code
    if status_code >= 500 and not app_settings.DEBUG:
        detail = "An internal error occurred"
    else:
        detail = exc.detail
    logger.error(f"HTTP Error {status_code}: {detail}")
    # Preserve any headers attached to the exception (e.g. ``Retry-After`` from
    # the heavy-endpoint rate limiter) instead of dropping them when rebuilding
    # the JSON body. Starlette only sends them when ``headers`` is explicitly
    # passed and non-empty.
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "status_code": status_code},
        headers=exc.headers if exc.headers else None,
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=exc)
    if not app_settings.DEBUG:
        detail = "An internal error occurred"
    else:
        detail = str(exc)
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "status_code": 500},
    )

static_dir = Path(app_settings.STATIC_DIR)
templates_dir = Path(app_settings.TEMPLATES_DIR)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.state.templates = Jinja2Templates(directory=str(templates_dir))
app.state.templates.env.globals["now"] = lambda: int(datetime.now(timezone.utc).timestamp())
# Expose the per-request CSRF token to every template (forms + <meta> tag).
from app.middleware.csrf import get_csrf_token  # noqa: E402

app.state.templates.env.globals["csrf_token"] = get_csrf_token
# Expose the per-request CSP nonce so inline <script>/<style> blocks can tag
# themselves; the SecurityHeadersMiddleware echoes the same value in the policy.
from app.middleware.security_headers import get_security_nonce  # noqa: E402

app.state.templates.env.globals["security_nonce"] = get_security_nonce


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return request.app.state.templates.TemplateResponse("login.html", {"request": request})


from app.routes import (
    admin,
    ai,
    api_keys,
    auth,
    datasources,
    designer,
    portal,
    preview,
    settings,
    versions,
)
from app.routes.api import query_builder

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(designer.router, prefix="/designer", tags=["designer"])
app.include_router(datasources.router, prefix="/datasources", tags=["datasources"])
app.include_router(preview.router, prefix="/preview", tags=["preview"])
app.include_router(ai.router, prefix="/ai", tags=["ai"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(versions.router, prefix="/designer/reports", tags=["versions"])
app.include_router(api_keys.router, tags=["api-keys"])
app.include_router(portal.router, prefix="/portal", tags=["portal"])
app.include_router(settings.router, prefix="/admin", tags=["settings"])
app.include_router(query_builder.router)


@app.get("/health")
async def health(
    response: Response,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Liveness/readiness probe that verifies the database is reachable.

    A bare ``{"status": "ok"}`` reports healthy even when the DB is down, so
    orchestrators keep routing traffic to a broken instance. Pinging the DB
    surfaces that as a 503 so the container can be restarted/unregistered.

    D3: when HEALTH_CHECK_TOKEN is configured, the probe requires it (Bearer
    header or ?token= query param) so it does not confirm liveness/readiness to
    unauthenticated attackers. When unset the endpoint stays public for probes
    that cannot carry a secret.
    """
    token = app_settings.HEALTH_CHECK_TOKEN
    if token:
        presented = None
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            presented = auth[7:].strip()
        else:
            presented = request.query_params.get("token")
        if not presented or not secrets.compare_digest(presented, token):
            response.status_code = status.HTTP_401_UNAUTHORIZED
            return {"status": "unauthorized"}

    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(f"Health check failed: {exc}")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unhealthy"}
    return {"status": "ok"}
