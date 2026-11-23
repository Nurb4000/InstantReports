"""TOTP (RFC 6238) / HOTP (RFC 4226) implementation for optional MFA.

Pure standard-library so the app needs no third-party auth dependency. Secrets
are base32 (the format authenticator apps such as Google Authenticator / Authy
expect, and what the ``b32secret`` in an ``otpauth://`` URI carries).

The implementation is intentionally small and explicit: TOTP is HOTP with the
counter derived from the clock (``floor(unix_time / period)``), so a reader can
verify correctness against the RFCs line by line.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse

DIGITS = 6
PERIOD_SECONDS = 30
# Tolerance (in clock ticks) on each side when validating, so a slightly skewed
# device clock still works. 1 => codes valid for ~90s centered on the minute.
DEFAULT_WINDOW = 1


def generate_secret(num_bytes: int = 16) -> str:
    """Return a fresh random base32-encoded secret.

    ``num_bytes`` of raw entropy becomes ``ceil(num_bytes * 8 / 5)`` base32
    characters (padded). 16 bytes => 128 bits of entropy, matching common
    authenticator defaults while exceeding the RFC-6238 minimum.
    """
    return base64.b32encode(secrets.token_bytes(num_bytes)).decode("ascii")


def _decode_secret(secret: str) -> bytes:
    """Decode a base32 secret (case-insensitive, padding-tolerant) to bytes."""
    normalized = secret.strip().upper().replace(" ", "")
    # base64.b32decode requires length that is a multiple of 8; pad with '='.
    padded = normalized + "=" * (-len(normalized) % 8)
    return base64.b32decode(padded)


def _hotp(secret_bytes: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 dynamic truncation of HMAC-SHA1(key, counter)."""
    counter_bytes = counter.to_bytes(8, "big")
    digest = hmac.new(secret_bytes, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = (
        (digest[offset] & 0x7F) << 24
        | digest[offset + 1] << 16
        | digest[offset + 2] << 8
        | digest[offset + 3]
    )
    return str(binary % (10**digits)).zfill(digits)


def generate_code(secret: str, now: float | None = None, digits: int = DIGITS) -> str:
    """Return the current TOTP code for ``secret`` (RFC 6238)."""
    timestamp = time.time() if now is None else now
    counter = int(timestamp // PERIOD_SECONDS)
    return _hotp(_decode_secret(secret), counter, digits)


def verify_code(
    secret: str,
    code: str,
    now: float | None = None,
    digits: int = DIGITS,
    period: int = PERIOD_SECONDS,
    window: int = DEFAULT_WINDOW,
) -> bool:
    """Validate ``code`` against ``secret``, tolerating small clock skew.

    Accepts the code for the current tick and ``window`` ticks on either side.
    Returns False for empty/malformed input rather than raising, so callers can
    treat a bad code as a normal auth failure.
    """
    if not code or not str(code).strip():
        return False
    code = str(code).strip()
    timestamp = time.time() if now is None else now
    counter = int(timestamp // period)
    for offset in range(-window, window + 1):
        if _hotp(_decode_secret(secret), counter + offset, digits) == code:
            return True
    return False


def provisioning_uri(
    secret: str,
    email: str,
    account_name: str = "InstantReports",
    digits: int = DIGITS,
    period: int = PERIOD_SECONDS,
) -> str:
    """Build an ``otpauth://`` URI for QR provisioning of authenticator apps."""
    query = urllib.parse.urlencode(
        {
            "secret": secret,
            "issuer": account_name,
            "algorithm": "SHA1",
            "digits": str(digits),
            "period": str(period),
        }
    )
    label = urllib.parse.quote(f"{account_name}:{email}", safe="")
    return f"otpauth://totp/{label}?{query}"


def otpauth_uri(
    email: str,
    secret: str,
    issuer: str = "InstantReports",
    digits: int = DIGITS,
    period: int = PERIOD_SECONDS,
) -> str:
    """Build an ``otpauth://`` URI keyed by ``(email, secret)``.

    Thin wrapper over :func:`provisioning_uri` with the more natural
    ``(email, secret, issuer)`` argument order used by MFA setup callers.
    """
    return provisioning_uri(
        secret, email, account_name=issuer, digits=digits, period=period
    )
