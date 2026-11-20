"""Unit tests for ``redact_connection_config`` (H2).

Ensures connection configs never leak plaintext secrets (passwords, bearer
tokens, basic-auth creds) when serialized into an API response, while keeping
non-secret connection metadata (host, port, database, user) intact.
"""
from __future__ import annotations

from app.services.connectors.base import redact_connection_config


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
