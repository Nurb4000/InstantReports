from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    MODE: Literal["designer", "runner"] = "designer"
    SEPARATE_MODE: bool = False  # If True, scheduler only runs in runner mode
    DEBUG: bool = False  # Enable debug mode (shows detailed errors)
    DATABASE_URL: str = "postgresql+asyncpg://ir:secret@localhost:5432/instantreports"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
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
    
    # Use mokapi for testing (set to false in production)
    USE_MOKAPI: bool = False

    # Static files
    STATIC_DIR: Path = BASE_DIR / "static"
    TEMPLATES_DIR: Path = BASE_DIR / "templates"


settings = Settings()
