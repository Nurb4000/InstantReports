"""Unit tests for the connection-encryption key-rotation support (M6).

M6: the at-rest encryption key is derived from ``SECRET_KEY`` unless a dedicated
``CONNECTION_SECRET_ENCRYPTION_KEY`` is set, so rotating ``SECRET_KEY`` silently
makes every stored connection credential undecryptable. These tests cover:

* ``fernet_for`` -- deterministic per-raw-key derivation (the basis for safe
  re-derivation during rotation),
* ``encrypt_config_with_key`` / ``decrypt_config_with_key`` -- the explicit-key
  read/write pair that lets an operator rotate the key with a re-encryption pass,
* ``_current_raw_key`` precedence -- a dedicated key decouples decryption from
  ``SECRET_KEY`` (so SECRET_KEY rotation no longer breaks anything), and
* ``_warn_connection_encryption_key`` -- the boot-time warning that surfaces the
  otherwise-silent hazard.
"""
from __future__ import annotations

import logging

import pytest

from app.config import settings
from app.main import _warn_connection_encryption_key
from app.services.connection_encryption import (
    decrypt_config_with_key,
    encrypt_config_with_key,
    fernet_for,
    looks_like_ciphertext,
)

OLD_KEY = "old-connection-key-000000000000000000"
NEW_KEY = "new-connection-key-000000000000000000"


# --- fernet_for determinism ---------------------------------------------------

def test_fernet_for_is_deterministic():
    # Same raw key -> interchangeable instances (so a stored credential can be
    # re-derived during rotation). Fernet encrypt is non-deterministic (random IV),
    # so we assert interchangeability, not identical ciphertext.
    a = fernet_for(OLD_KEY)
    b = fernet_for(OLD_KEY)
    token = a.encrypt(b"x")
    assert b.decrypt(token) == b"x"


def test_fernet_for_is_isolated_per_raw_key():
    ct_old = fernet_for(OLD_KEY).encrypt(b"secret")
    ct_new = fernet_for(NEW_KEY).encrypt(b"secret")
    assert ct_old != ct_new
    # Each key only decrypts its own tokens.
    assert fernet_for(OLD_KEY).decrypt(ct_old) == b"secret"
    assert fernet_for(NEW_KEY).decrypt(ct_new) == b"secret"


# --- explicit-key rotation round trip -----------------------------------------

ROTATE_CONFIG = {
    "host": "db.internal",
    "password": "p@ssw0rd",
    "connection_string": "postgresql://app:supersecret@db.internal:5432/app",
    "url": "https://example.com/hook",
}


def test_rotate_config_round_trip():
    # Store under OLD key, read back with OLD, re-store under NEW, read with NEW.
    stored = encrypt_config_with_key(ROTATE_CONFIG, OLD_KEY)
    assert looks_like_ciphertext(stored["password"])
    assert looks_like_ciphertext(stored["connection_string"])  # value-based too
    assert stored["host"] == "db.internal" and stored["url"] == ROTATE_CONFIG["url"]

    assert decrypt_config_with_key(stored, OLD_KEY) == ROTATE_CONFIG
    rotated = encrypt_config_with_key(decrypt_config_with_key(stored, OLD_KEY), NEW_KEY)
    assert decrypt_config_with_key(rotated, NEW_KEY) == ROTATE_CONFIG


def test_old_key_cannot_read_rotated_config():
    rotated = encrypt_config_with_key(ROTATE_CONFIG, NEW_KEY)
    # Fail-open: the old key leaves the ciphertext unchanged (still not plaintext).
    assert decrypt_config_with_key(rotated, OLD_KEY)["password"] != "p@ssw0rd"


def test_rotate_handles_empty_and_none():
    assert encrypt_config_with_key({}, OLD_KEY) == {}
    assert encrypt_config_with_key(None, OLD_KEY) == {}
    assert decrypt_config_with_key(None, OLD_KEY) == {}


# --- _current_raw_key precedence (decoupling from SECRET_KEY) -----------------

def test_current_raw_key_prefers_dedicated_key(monkeypatch):
    from app.services.connection_encryption import _current_raw_key

    original_secret = settings.SECRET_KEY
    original_dedicated = settings.CONNECTION_SECRET_ENCRYPTION_KEY
    try:
        monkeypatch.setattr(settings, "CONNECTION_SECRET_ENCRYPTION_KEY", OLD_KEY)
        monkeypatch.setattr(settings, "SECRET_KEY", "some-other-jwt-key-000000000")
        # The dedicated key wins, so rotating the JWT secret does not change the
        # credential-encryption key (the core M6 mitigation).
        assert _current_raw_key() == OLD_KEY
        # Re-derived instance can decrypt what the dedicated key encrypted.
        token = fernet_for(OLD_KEY).encrypt(b"y")
        assert fernet_for(_current_raw_key()).decrypt(token) == b"y"
    finally:
        monkeypatch.setattr(settings, "CONNECTION_SECRET_ENCRYPTION_KEY", original_dedicated)
        monkeypatch.setattr(settings, "SECRET_KEY", original_secret)


# --- boot-time warning --------------------------------------------------------

def test_warning_emitted_when_no_dedicated_key(monkeypatch, caplog):
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "CONNECTION_SECRET_ENCRYPTION_KEY", "")
    assert _warn_connection_encryption_key(logging.getLogger("m6-test")) is True
    assert "CONNECTION_SECRET_ENCRYPTION_KEY" in caplog.text


def test_no_warning_when_dedicated_key_set(monkeypatch, caplog):
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "CONNECTION_SECRET_ENCRYPTION_KEY", "dedicated-key-0000000000")
    assert _warn_connection_encryption_key(logging.getLogger("m6-test")) is False
    assert "CONNECTION_SECRET_ENCRYPTION_KEY" not in caplog.text
