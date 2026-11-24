"""Unit tests for ``redact_connection_config`` (H2).

Ensures connection configs never leak plaintext secrets (passwords, bearer
tokens, basic-auth creds) when serialized into an API response, while keeping
non-secret connection metadata (host, port, database, user) intact.
"""
from __future__ import annotations

from app.services.connectors.base import is_secret, redact_connection_config


def test_none_and_empty_return_empty_dict():
    assert redact_connection_config(None) == {}
    assert redact_connection_config({}) == {}


def test_secret_values_are_masked():
    config = {
        "host": "db.example.com",
        "port": 5432,
        "database": "sales",
        "user": "reporter",
        "password": "s3cr3t",
        "auth_token": "eyJhbGci...",
        "auth_password": "pw",
    }
    redacted = redact_connection_config(config)

    # Secrets masked, metadata preserved.
    assert redacted["password"] == "***"
    assert redacted["auth_token"] == "***"
    assert redacted["auth_password"] == "***"
    assert redacted["host"] == "db.example.com"
    assert redacted["port"] == 5432
    assert redacted["database"] == "sales"
    assert redacted["user"] == "reporter"


def test_original_config_is_not_mutated():
    config = {"host": "h", "password": "p", "api-key": "k"}
    redact_connection_config(config)
    # Caller's dict is untouched (helper returns a copy).
    assert config["password"] == "p"
    assert config["api-key"] == "k"


def test_various_secret_key_names_are_captured():
    config = {
        "passwd": "a",
        "pwd": "b",
        "secret": "c",
        "client_secret": "d",
        "access_key": "e",
        "secret_key": "f",
        "authorization": "g",
        "private_key": "h",
        "credential": "i",
        "username": "j",  # not a secret name
    }
    redacted = redact_connection_config(config)
    for key in config:
        if key == "username":
            assert redacted[key] == "j"
        else:
            assert redacted[key] == "***"


# --- M8: value-based detection (credential embedded under an opaque key) ------


def test_value_based_detection_flags_embedded_credentials():
    from app.services.connectors.base import _looks_like_secret_value

    assert _looks_like_secret_value("postgresql://user:PASSWORD@host/db")
    assert _looks_like_secret_value("mongodb+srv://admin:secret@cluster.example.com/")
    assert _looks_like_secret_value("ftp://user:pw@sftp.example.com")
    # Not a secret: no embedded user:password@ authority, or not a string.
    assert not _looks_like_secret_value("https://example.com/hook")
    assert not _looks_like_secret_value("db.internal")
    assert not _looks_like_secret_value("app")
    assert not _looks_like_secret_value(12345)
    assert not _looks_like_secret_value("")


def test_is_secret_combines_key_and_value():
    assert is_secret("password", "anything")
    assert is_secret("connection_string", "postgresql://u:p@h/db")
    assert not is_secret("url", "https://example.com/hook")
    assert not is_secret("host", "db.internal")


def test_opaque_key_with_embedded_credential_is_masked():
    config = {
        "connection_string": "postgresql://app:supersecret@db.internal:5432/app",
        "dsn": "ftp://reader:readder@sftp.example.com/pub",
        "url": "https://example.com/hook",  # public, no embedded creds
        "host": "db.internal",
    }
    redacted = redact_connection_config(config)
    assert redacted["connection_string"] == "***"
    assert redacted["dsn"] == "***"
    # Public URL and metadata pass through untouched.
    assert redacted["url"] == "https://example.com/hook"
    assert redacted["host"] == "db.internal"


def test_value_based_mask_does_not_mutate_original():
    config = {"connection_string": "postgresql://u:p@h/db"}
    redact_connection_config(config)
    assert config["connection_string"] == "postgresql://u:p@h/db"
