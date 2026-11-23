"""Encryption-at-rest for connection credentials (H2).

Connection secrets live in two places, both historically plaintext:

  * the JSONB ``DataConnection.config`` blob (DB passwords, REST bearer tokens,
    SMTP creds, ...), and
  * the ``secret_value`` column of ``connection_credential_rotations`` (the
    retired value retained during a rotation grace window).

Encryption-at-rest means the database only ever stores ciphertext, so a
compromised DB dump does not hand over credentials. This module provides:

  * ``EncryptedSecretsJSON`` / ``EncryptedText`` -- SQLAlchemy ``TypeDecorator``
    types that transparently encrypt secret values on write and decrypt them on
    read. Application code (connectors, the redaction layer, rotation helpers)
    always sees plaintext; only the stored bytes are ciphertext. Because the
    compiled column type is unchanged (JSONB / VARCHAR), no schema migration is
    required -- only a data re-encryption (see ``scripts/reencrypt_connection_secrets.py``).
  * ``encrypt_secret`` / ``decrypt_secret`` / ``looks_like_ciphertext`` -- the
    primitives the decorators build on.
  * ``encrypt_config_secrets`` / ``decrypt_config_secrets`` -- operate on a whole
    config dict, reusing the same secret-key matcher as ``redact_connection_config``
    (``_SECRET_KEY_RE``) so storage and response-masking agree on what is a secret.

Encryption is Fernet (authenticated AES-128-CBC + HMAC). Decryption is
fail-open: any value that is not a decryptable Fernet token is returned unchanged,
so plaintext written before the re-encryption migration still round-trips after
upgrade.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, VARCHAR

from app.config import settings

# ``_SECRET_KEY_RE`` lives in ``connectors.base`` (shared with ``redact_connection_config``)
# but importing it at module load time creates a cycle -- the connectors package
# ``__init__`` imports the connectors, which import ``connection_credentials``,
# which imports ``models.connection`` (this module's owner) while it is still
# being defined. Import lazily inside ``_is_secret_key`` so it resolves once every
# module has finished loading.
_SECRET_KEY_RE = None


def _get_secret_key_re():
    global _SECRET_KEY_RE
    if _SECRET_KEY_RE is None:
        from app.services.connectors.base import _SECRET_KEY_RE as _re
        _SECRET_KEY_RE = _re
    return _SECRET_KEY_RE

# Fernet tokens are urlsafe-base64 and always start with "gAAAAA" (the version
# byte 0x80). Used only as a fast pre-filter; the real safety net is the
# try/except around ``Fernet.decrypt`` (fail-open on InvalidToken).
_CIPHERTEXT_PREFIX = "gAAAA"

_fernet_cache: dict[bytes, Fernet] = {}


def _fernet() -> Fernet:
    """Return a process-wide ``Fernet`` for the configured key.

    The concrete Fernet key is ``urlsafe_b64(sha256(raw))`` so any provided string
    -- or the default ``SECRET_KEY`` -- yields a valid 256-bit key. Cached per raw
    key so repeated reads/writes reuse one instance.
    """
    raw = (
        settings.CONNECTION_SECRET_ENCRYPTION_KEY
        or settings.SECRET_KEY
        or "instantreports-default-connection-encryption-key"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    fer = _fernet_cache.get(key)
    if fer is None:
        fer = Fernet(key)
        _fernet_cache[key] = fer
    return fer


def reset_encryption_cache() -> None:
    """Drop cached ``Fernet`` instances.

    Primarily for tests that change ``CONNECTION_SECRET_ENCRYPTION_KEY`` between
    cases; production never changes the key at runtime.
    """
    _fernet_cache.clear()


def looks_like_ciphertext(value: Any) -> bool:
    """Heuristic: does ``value`` look like a Fernet token?"""
    return isinstance(value, str) and value.startswith(_CIPHERTEXT_PREFIX)


def encrypt_secret(value: str) -> str:
    """Encrypt a single secret string, returning a urlsafe-base64 ciphertext str."""
    if not isinstance(value, str):
        raise TypeError(f"encrypt_secret expects str, got {type(value).__name__}")
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: Any) -> str:
    """Decrypt a Fernet token; fail open (return the value unchanged) otherwise.

    Fail-open is deliberate: it lets values written in plaintext before the
    re-encryption migration still round-trip after upgrade, and makes a corrupted
    or wrong-key value degrade to "still usable plaintext" rather than raising.
    """
    if not looks_like_ciphertext(value):
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return value


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and bool(_get_secret_key_re().search(key))


def _encrypt_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _encrypt_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encrypt_value(key, v) for v in value]
    # Only encrypt non-empty string values whose key is recognised as a secret.
    # Empty strings and non-secret keys are stored verbatim.
    if _is_secret_key(key) and isinstance(value, str) and value != "":
        return encrypt_secret(value)
    return value


def _decrypt_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _decrypt_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decrypt_value(key, v) for v in value]
    # Only decrypt values stored under a secret key; non-secret keys are never
    # encrypted, so their (plaintext) values are left untouched.
    if _is_secret_key(key) and isinstance(value, str):
        return decrypt_secret(value)
    return value


def encrypt_config_secrets(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``config`` with secret-valued keys encrypted."""
    if not config:
        return {}
    return {k: _encrypt_value(k, v) for k, v in config.items()}


def decrypt_config_secrets(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``config`` with ciphertext secret values decrypted."""
    if not config:
        return {}
    return {k: _decrypt_value(k, v) for k, v in config.items()}


def reencrypt_config_secrets(config: dict[str, Any] | None) -> dict[str, Any]:
    """Idempotent re-encryption for migrations.

    Encrypts only *plaintext* secret values; values that are already ciphertext
    (or non-secret / empty) are left untouched, so running this over data that is
    already partially encrypted never double-encrypts a value. Used by the
    at-rest re-encryption migration to pass safely over mixed rows.
    """
    if not config:
        return {}
    out = {}
    for key, value in config.items():
        if _is_secret_key(key) and isinstance(value, str) and value and not looks_like_ciphertext(value):
            out[key] = encrypt_secret(value)
        else:
            out[key] = value
    return out


def reencrypt_secret_value(value: Any) -> Any:
    """Idempotent re-encryption for a single secret string (rotation column)."""
    if isinstance(value, str) and value and not looks_like_ciphertext(value):
        return encrypt_secret(value)
    return value


class EncryptedSecretsJSON(TypeDecorator):
    """JSONB column that encrypts secret-valued keys and decrypts on read.

    Non-secret fields (host, port, database, user, ...) are stored verbatim; only
    values whose key matches ``_SECRET_KEY_RE`` are encrypted. Reads always return
    plaintext to the application, so connectors and the response redaction layer
    see exactly what they expect while the database stores ciphertext. The compiled
    column type is unchanged (JSONB), so this is a data-only change.
    """

    impl = JSONB  # compiles to JSONB on postgres (unchanged column type)
    cache_ok = False

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_config_secrets(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_config_secrets(value)


class EncryptedText(TypeDecorator):
    """Text/VARCHAR column that encrypts its value on write and decrypts on read.

    Used for single-secret columns such as the retained rotation ``secret_value``.
    """

    impl = VARCHAR
    cache_ok = False

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_secret(value)
