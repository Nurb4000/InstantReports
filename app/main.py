from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings as app_settings
from app.database import engine
from app.models import *  # noqa: F401,F403 - ensure all models are imported for Alembic

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="InstantReports",
    description="Report design, scheduling, and delivery platform",
    version="0.1.0",
    lifespan=lifespan,
    debug=app_settings.MODE == "designer",  # Enable debug only in designer mode
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom exception handler to hide sensitive error details
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"HTTP Error {exc.status_code}: {exc.detail}")
    if app_settings.MODE == "runner" or not app_settings.DEBUG:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": "An error occurred"},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    if app_settings.MODE == "runner" or not app_settings.DEBUG:
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred"},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )

static_dir = Path(app_settings.STATIC_DIR)
templates_dir = Path(app_settings.TEMPLATES_DIR)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.state.templates = Jinja2Templates(directory=str(templates_dir))
app.state.templates.env.globals["mode"] = app_settings.MODE
app.state.templates.env.globals["now"] = lambda: int(datetime.now().timestamp())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return request.app.state.templates.TemplateResponse("login.html", {"request": request})


if app_settings.MODE == "designer":
    from app.routes import auth, designer, datasources, preview, ai, admin, versions, api_keys, portal, settings  # noqa: F401

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

elif app_settings.MODE == "runner":
    from app.routes import auth, portal, admin, api_keys  # noqa: F401

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(portal.router, prefix="/portal", tags=["portal"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(api_keys.router, tags=["api-keys"])


@app.on_event("startup")
async def startup_event():
    # Start scheduler in runner mode OR if SEPARATE_MODE is disabled (dev mode)
    if app_settings.MODE == "runner" or not app_settings.SEPARATE_MODE:
        from app.runner import run_scheduler
        import asyncio
        asyncio.create_task(run_scheduler())


@app.get("/health")
async def health():
    return {"status": "ok", "mode": app_settings.MODE}
