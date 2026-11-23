"""Unit tests for encryption-at-rest of connection credentials (H2).

Covers the Fernet primitives, whole-config encrypt/decrypt, fail-open behaviour,
cross-key isolation, and the ``EncryptedSecretsJSON`` / ``EncryptedText``
TypeDecorators used on ``DataConnection.config`` and the rotation secret column.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services.connection_encryption import (
    EncryptedSecretsJSON,
    EncryptedText,
    decrypt_config_secrets,
    decrypt_secret,
    encrypt_config_secrets,
    encrypt_secret,
    looks_like_ciphertext,
    reset_encryption_cache,
)


# --- single-secret primitives -------------------------------------------------

def test_encrypt_decrypt_round_trip():
    ct = encrypt_secret("s3cret-token")
    assert ct != "s3cret-token"
    assert decrypt_secret(ct) == "s3cret-token"


def test_looks_like_ciphertext_only_matches_fernet_tokens():
    assert looks_like_ciphertext(encrypt_secret("x"))
    assert not looks_like_ciphertext("plain-password")
    assert not looks_like_ciphertext("")
    assert not looks_like_ciphertext(1234)


def test_decrypt_fails_open_on_plaintext():
    # A value written before the re-encryption migration is still usable.
    assert decrypt_secret("plaintext-value") == "plaintext-value"


def test_decrypt_fails_open_on_invalid_token():
    garbage = "gAAAAAbnot-a-real-token-zzz"
    # Does not raise; returns the value unchanged when it cannot be decrypted.
    assert decrypt_secret(garbage) == garbage


def test_encrypt_is_non_deterministic():
    # Fernet embeds a timestamp + random IV, so repeated enciphers differ.
    assert encrypt_secret("same") != encrypt_secret("same")


def test_encrypt_rejects_non_string():
    with pytest.raises(TypeError):
        encrypt_secret(123)  # type: ignore[arg-type]


def test_cross_key_isolation_and_fail_open(monkeypatch):
    """Ciphertext under one key does not decrypt under another (stays fail-open)."""
    original = settings.CONNECTION_SECRET_ENCRYPTION_KEY
    try:
        monkeypatch.setattr(settings, "CONNECTION_SECRET_ENCRYPTION_KEY", "key-alpha-00000000000000")
        reset_encryption_cache()
        ct_alpha = encrypt_secret("shared-secret")

        monkeypatch.setattr(settings, "CONNECTION_SECRET_ENCRYPTION_KEY", "key-bravo-0000000000000")
        reset_encryption_cache()
        ct_bravo = encrypt_secret("shared-secret")

        # Different keys -> different ciphertext.
        assert ct_alpha != ct_bravo
        # Decrypting alpha's token while keyed for bravo fails open (unchanged).
        assert decrypt_secret(ct_alpha) == ct_alpha
        # ...and bravo's token still decrypts under bravo.
        assert decrypt_secret(ct_bravo) == "shared-secret"
    finally:
        monkeypatch.setattr(settings, "CONNECTION_SECRET_ENCRYPTION_KEY", original)
        reset_encryption_cache()


# --- whole-config encrypt/decrypt ---------------------------------------------

SECRET_CONFIG = {
    "host": "db.internal",
    "port": 5432,
    "database": "sales",
    "user": "reporter",
    "password": "p@ssw0rd",
    "auth_token": "bearer-xyz",
    "auth_password": "basic-pw",
}


def test_config_encryption_masks_only_secret_keys():
    enc = encrypt_config_secrets(SECRET_CONFIG)
    # Non-secret metadata passes through verbatim.
    assert enc["host"] == "db.internal"
    assert enc["port"] == 5432
    assert enc["database"] == "sales"
    assert enc["user"] == "reporter"
    # Secret values become ciphertext.
    for key in ("password", "auth_token", "auth_password"):
        assert enc[key] != SECRET_CONFIG[key]
        assert looks_like_ciphertext(enc[key])


def test_config_encryption_is_reversible():
    assert decrypt_config_secrets(encrypt_config_secrets(SECRET_CONFIG)) == SECRET_CONFIG


def test_config_does_not_mutate_input():
    original = dict(SECRET_CONFIG)
    encrypt_config_secrets(SECRET_CONFIG)
    decrypt_config_secrets(SECRET_CONFIG)
    assert SECRET_CONFIG == original


def test_empty_and_none_configs():
    assert encrypt_config_secrets(None) == {}
    assert encrypt_config_secrets({}) == {}
    assert decrypt_config_secrets(None) == {}
    assert decrypt_config_secrets({}) == {}


def test_empty_secret_values_are_not_encrypted():
    enc = encrypt_config_secrets({"password": "", "host": "h"})
    assert enc["password"] == ""  # nothing to hide
    assert enc["host"] == "h"


def test_non_secret_key_with_secret_looking_value_is_not_encrypted():
    # The key name drives masking/encryption, not the value.
    enc = encrypt_config_secrets({"host": "s3cret-value"})
    assert enc["host"] == "s3cret-value"


def test_nested_dict_secret_values_are_encrypted():
    nested = {"auth": {"password": "deep", "host": "h"}}
    enc = encrypt_config_secrets(nested)
    assert looks_like_ciphertext(enc["auth"]["password"])
    assert enc["auth"]["host"] == "h"
    assert decrypt_config_secrets(enc) == nested


# --- TypeDecorators -----------------------------------------------------------

def test_encrypted_secret_json_decorator_round_trip():
    deco = EncryptedSecretsJSON()
    config = dict(SECRET_CONFIG)
    bound = deco.process_bind_param(config, None)
    assert isinstance(bound, dict)
    assert looks_like_ciphertext(bound["password"])
    assert bound["host"] == "db.internal"
    # Read path returns plaintext to the application.
    assert deco.process_result_value(bound, None) == config


def test_encrypted_secret_json_decorator_handles_none():
    deco = EncryptedSecretsJSON()
    assert deco.process_bind_param(None, None) is None
    assert deco.process_result_value(None, None) is None


def test_encrypted_text_decorator_round_trip():
    deco = EncryptedText()
    bound = deco.process_bind_param("s3cret", None)
    assert looks_like_ciphertext(bound)
    assert deco.process_result_value(bound, None) == "s3cret"


def test_encrypted_text_decorator_handles_none_and_non_string():
    deco = EncryptedText()
    assert deco.process_bind_param(None, None) is None
    assert deco.process_result_value(None, None) is None
    # Non-string values are stringified then encrypted/decrypted.
    assert deco.process_result_value(deco.process_bind_param(42, None), None) == "42"


# --- idempotent re-encryption (migration helper) ------------------------------

from app.services.connection_encryption import (  # noqa: E402
    reencrypt_config_secrets,
    reencrypt_secret_value,
)


def test_reencrypt_only_encodes_plaintext_secrets():
    enc = reencrypt_config_secrets(SECRET_CONFIG)
    for key in ("password", "auth_token", "auth_password"):
        assert looks_like_ciphertext(enc[key])
    # Non-secret metadata untouched.
    assert enc["host"] == "db.internal"


def test_reencrypt_is_idempotent_on_already_encrypted_config():
    once = reencrypt_config_secrets(SECRET_CONFIG)
    twice = reencrypt_config_secrets(once)
    # A second pass must not double-encrypt: ciphertext stays byte-identical.
    assert twice == once
    # And still decrypts back to the original config.
    assert decrypt_config_secrets(twice) == SECRET_CONFIG


def test_reencrypt_leaves_plaintext_alongside_ciphertext():
    # Mixed state (some already encrypted, some not) -- only plaintext is touched.
    mixed = {"password": "plain", "auth_token": encrypt_secret("kept")}
    out = reencrypt_config_secrets(mixed)
    assert looks_like_ciphertext(out["password"])
    # The already-ciphertext value is left byte-identical (not re-encrypted).
    assert out["auth_token"] == mixed["auth_token"]
    # Full decryption recovers the original plaintext values.
    assert decrypt_config_secrets(out) == {"password": "plain", "auth_token": "kept"}


def test_reencrypt_secret_value_idempotent():
    assert reencrypt_secret_value("plain") != "plain"
    enc = reencrypt_secret_value("plain")
    assert reencrypt_secret_value(enc) == enc  # not double-encrypted
    assert decrypt_secret(enc) == "plain"


def test_reencrypt_secret_value_passes_through_ciphertext_and_empty():
    enc = encrypt_secret("x")
    assert reencrypt_secret_value(enc) == enc
    assert reencrypt_secret_value("") == ""
    assert reencrypt_secret_value(None) is None
