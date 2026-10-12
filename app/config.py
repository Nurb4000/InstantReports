from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars (e.g., DB_PASSWORD from docker-compose)
    )

    DEBUG: bool = False  # Enable debug mode (shows detailed errors)
    DATABASE_URL: str = "postgresql+asyncpg://ir:secret@localhost:5432/instantreports"
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"

    # CORS: comma-separated list of allowed browser origins. Empty (the default)
    # means "same-origin only" — no cross-origin requests are permitted, which is
    # safe for the bundled same-origin UI. Never use "*" together with
    # credentials: Starlette then reflects the caller's Origin back, letting any
    # site make authenticated requests.
    CORS_ALLOW_ORIGINS: str = ""

    # Auth cookies: mark them Secure (HTTPS-only) in production. Set to false
    # only for local development over plain HTTP.
    COOKIE_SECURE: bool = True


    @field_validator("SECRET_KEY")
    @classmethod
    def _reject_placeholder_secret(cls, v: str) -> str:
        # The JWT signing secret must never be empty or the well-known development
        # placeholder: with it, anyone can forge an admin token (full auth
        # bypass). Fail loudly at startup instead of shipping with a weak key.
        if not v:
            raise ValueError(
                "SECRET_KEY is not set. Set a strong, private SECRET_KEY via "
                "environment variable or .env before starting the app."
            )
        weak = {
            "change-me-in-production",
            "your-secret-key-here",
            "secret",
            "changeme",
            "admin",
        }
        if v in weak or len(v) < 16:
            raise ValueError(
                "SECRET_KEY is too short or still a placeholder/weak value. "
                "Set a strong, random SECRET_KEY (at least 16 characters) before "
                "starting the app."
            )
        return v
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # LDAP
    LDAP_URL: str = ""
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""
    LDAP_SEARCH_BASE: str = ""
    LDAP_USER_ATTR: str = "uid"
    LDAP_EMAIL_ATTR: str = "mail"
    LDAP_NAME_ATTR: str = "cn"

    # AI (OpenAI-compatible)
    AI_BASE_URL: str = "http://localhost:8080/v1"
    AI_API_KEY: str = "none"
    AI_MODEL: str = "local-model"
    AI_ENABLED: bool = False

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "reports@example.com"
    SMTP_USE_TLS: bool = True
    SMTP_SUBJECT_TEMPLATE: str = "Report: {{report_name}}"
    SMTP_BODY_TEMPLATE: str = "Please find the attached report: {{report_name}}\n\nGenerated at: {{generated_at}}"

    # Timezone
    TIMEZONE: str = "America/New_York"  # EST/EDT

    # Report storage
    REPORT_RETENTION_DAYS: int = 90

    # Runner mode: how often (seconds) the scheduler re-syncs schedules from the
    # DB so new/changed schedules take effect without a runner restart.
    SCHEDULE_SYNC_INTERVAL_SECONDS: int = 60

    # Login rate limiting (protect against brute force). A single-process
    # in-memory limiter; multiple workers do not share state.
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Preview row limits: cap rows returned in preview/export to avoid memory
    # issues with large datasets. Tables show up to this many rows; charts limit
    # bars/slices to this count.
    PREVIEW_TABLE_ROW_LIMIT: int = 50
    PREVIEW_CHART_ROW_LIMIT: int = 10

    # Static files
    STATIC_DIR: Path = BASE_DIR / "static"
    TEMPLATES_DIR: Path = BASE_DIR / "templates"

    # CSV/Excel connector data directory: file_path values are resolved relative
    # to this directory and must not escape it via ``..`` segments.  Defaults to
    # the repo's ``data/`` folder; override via env for production deployments.
    DATA_DIR: Path = BASE_DIR / "data"

    # Comma-separated list of allowed private IP ranges for outbound HTTP calls
    # (webhooks, REST connectors).  Empty means "public only".  Common values:
    # ``10.0.0.0/8``, ``172.16.0.0/12``, ``192.168.0.0/16``, ``127.0.0.0/8``.
    # Default includes loopback so local dev/test servers are reachable.
    ALLOWED_PRIVATE_RANGES: str = "127.0.0.0/8"

    # Image uploads: hard cap and the image formats we accept. The actual
    # format is detected from the file's magic bytes, never from the
    # client-supplied filename or Content-Type.
    MAX_IMAGE_UPLOAD_BYTES: int = 5 * 1024 * 1024


settings = Settings()
