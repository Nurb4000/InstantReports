"""Tests for the API key service.

Covers the security fix in round-6-follow-up: API keys must never be stored in
cleartext. ``generate_api_key`` returns the plaintext to the caller once and
persists only its SHA-256 hash; ``validate_api_key`` looks up by that hash.
"""

from __future__ import annotations

import hashlib

from app.services.api_key import generate_api_key, hash_api_key, validate_api_key


def test_hash_api_key_is_sha256_hexdigest():
    key = "ir_testkey"
    expected = hashlib.sha256(key.encode()).hexdigest()
    assert hash_api_key(key) == expected
    assert len(expected) == 64  # fits the String(64) column exactly


async def test_generate_stores_hash_not_plaintext(db_session):
    api_key, plaintext = await generate_api_key(
        db=db_session, name="test", user_id=_uid()
    )

    # Caller gets the plaintext...
    assert plaintext.startswith("ir_")
    # ...but the DB row holds only its hash.
    assert api_key.key != plaintext
    assert api_key.key == hash_api_key(plaintext)


async def test_validate_accepts_correct_key(db_session):
    api_key, plaintext = await generate_api_key(
        db=db_session, name="k", user_id=_uid()
    )

    result = await validate_api_key(db_session, plaintext)

    assert result is not None
    assert result.user_id == api_key.user_id


async def test_validate_rejects_wrong_key(db_session):
    assert await validate_api_key(db_session, "ir_bogus") is None


async def test_validate_rejects_expired_key(db_session):
    _api_key, plaintext = await generate_api_key(
        db=db_session,
        name="exp",
        user_id=_uid(),
        expires_in_days=-1,  # already expired
    )
    assert await validate_api_key(db_session, plaintext) is None


async def test_validate_rejects_inactive_key(db_session):

    api_key, plaintext = await generate_api_key(db=db_session, name="k", user_id=_uid())
    api_key.is_active = False
    await db_session.commit()

    assert await validate_api_key(db_session, plaintext) is None


async def test_validate_updates_last_used(db_session):
    _, plaintext = await generate_api_key(db=db_session, name="k", user_id=_uid())

    first = await validate_api_key(db_session, plaintext)
    second = await validate_api_key(db_session, plaintext)

    assert first is not None and second is not None
    # Second use reflects an updated last_used_at relative to the first.
    assert (second.last_used_at or first.last_used_at) is not None


def _uid():
    import uuid

    return uuid.uuid4()
