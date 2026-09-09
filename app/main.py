from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings as app_settings
from app.database import Base
from app.models import *

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-create tables and seed admin user on startup (dev convenience)
    try:
        engine = create_async_engine(app_settings.DATABASE_URL)
        async with engine.begin() as conn:
            try:
                await conn.run_sync(Base.metadata.create_all)
            except Exception as create_err:
                # Tables may already exist from migrations - that's OK
                logger.info(f"Tables may already exist: {create_err}")
        await engine.dispose()
        logger.info("Database tables ensured (created if missing)")

        # Seed admin user and default northwind connection if not exists
        from app.auth import hash_password
        from app.database import async_session_factory
        from app.models.connection import DataConnection
        from app.models.user import AuthSource, User, UserRole
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        import uuid

        async with async_session_factory() as db:
            # Seed admin user
            result = await db.execute(select(User).where(User.email == "admin@example.com"))
            if not result.scalar_one_or_none():
                admin = User(
                    email="admin@example.com",
                    name="Admin",
                    password_hash=hash_password("admin"),
                    role=UserRole.ADMIN,
                    auth_source=AuthSource.LOCAL,
                    is_active=True,
                )
                db.add(admin)
                try:
                    await db.commit()
                    logger.info("Seeded admin user (admin@example.com / admin)")
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
    yield


app = FastAPI(
    title="InstantReports",
    description="Report design, scheduling, and delivery platform",
    version="0.1.0",
    lifespan=lifespan,
    debug=app_settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "status_code": status_code},
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


@app.on_event("startup")
async def startup_event():
    import asyncio

    from app.runner import run_scheduler
    asyncio.create_task(run_scheduler())


@app.get("/health")
async def health():
    return {"status": "ok"}
