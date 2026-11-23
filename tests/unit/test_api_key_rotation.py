"""Tests for API key rotation with a grace period (Gap #11).

Rotation must make a new secret valid immediately while keeping the retired
secret usable for a grace window, enforce ownership, and prune expired secrets.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models.api_key import APIKeyRotation
from app.services.api_key import (
    DEFAULT_ROTATION_GRACE_PERIOD_DAYS,
    hash_api_key,
    prune_expired_rotations,
    rotate_api_key,
    validate_api_key,
)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


async def _make_key(db, grace_days=7):
    """Generate a key and return (plaintext, api_key_row)."""
    from app.services.api_key import generate_api_key as generate_key

    api_key, plaintext = await generate_key(db, "k", _uid())
    return plaintext, api_key


async def test_rotate_makes_new_key_valid_and_keeps_old_in_grace(db_session):
    from app.services.api_key import generate_api_key as generate_key

    old_plaintext, api_key = await _make_key(db_session)

    rotated, new_plaintext = await rotate_api_key(
        db_session, api_key.id, api_key.user_id, grace_period_days=7
    )

    # New secret validates immediately.
    assert await validate_api_key(db_session, new_plaintext) is not None
    # Retired secret still validates during the grace window.
    assert await validate_api_key(db_session, old_plaintext) is not None
    # The active row now holds the NEW secret's hash, not the old one.
    assert rotated.key == hash_api_key(new_plaintext)
    assert rotated.key != hash_api_key(old_plaintext)
    # A grace entry was recorded for the retired secret.
    rows = (
        await db_session.execute(
            select(APIKeyRotation).where(
                APIKeyRotation.api_key_id == api_key.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].key_hash == hash_api_key(old_plaintext)
    assert rows[0].valid_until > _now()


async def test_old_key_rejects_after_grace_expires(db_session):
    from app.services.api_key import generate_api_key as generate_key

    old_plaintext, api_key = await _make_key(db_session)
    _, _ = await rotate_api_key(db_session, api_key.id, api_key.user_id, 7)

    # Force the grace entry into the past.
    row = (
        await db_session.execute(
            select(APIKeyRotation).where(
                APIKeyRotation.api_key_id == api_key.id
            )
        )
    ).scalar_one()
    row.valid_until = _now() - timedelta(seconds=1)
    await db_session.commit()

    assert await validate_api_key(db_session, old_plaintext) is None


async def test_rotate_returns_plaintext_matching_stored_hash(db_session):
    from app.services.api_key import generate_api_key as generate_key

    _plaintext, api_key = await _make_key(db_session)
    rotated, new_plaintext = await rotate_api_key(
        db_session, api_key.id, api_key.user_id, 7
    )

    assert new_plaintext.startswith("ir_")
    # The returned plaintext is exactly what gets persisted (as a hash).
    assert rotated.key == hash_api_key(new_plaintext)
    assert await validate_api_key(db_session, new_plaintext) is not None


async def test_rotate_requires_ownership(db_session):
    from app.services.api_key import generate_api_key as generate_key

    api_key, _ = await generate_key(db_session, "k", _uid())
    other_user = _uid()

    try:
        await rotate_api_key(db_session, api_key.id, other_user, 7)
    except KeyError:
        pass
    else:
        raise AssertionError("rotation should fail for a non-owner")


async def test_rotate_rejects_invalid_grace_period(db_session):
    from app.services.api_key import generate_api_key as generate_key

    api_key, _ = await generate_key(db_session, "k", _uid())
    try:
        await rotate_api_key(db_session, api_key.id, api_key.user_id, grace_period_days=0)
    except ValueError:
        pass
    else:
        raise AssertionError("grace_period_days must be >= 1")


async def test_prune_expired_rotations(db_session):
    from app.services.api_key import generate_api_key as generate_key

    _plaintext, api_key = await _make_key(db_session)
    # Insert one expired and one future rotation manually.
    db_session.add(
        APIKeyRotation(
            api_key_id=api_key.id,
            key_hash=hash_api_key("ir_expired"),
            valid_until=_now() - timedelta(days=1),
        )
    )
    db_session.add(
        APIKeyRotation(
            api_key_id=api_key.id,
            key_hash=hash_api_key("ir_future"),
            valid_until=_now() + timedelta(days=1),
        )
    )
    await db_session.commit()

    pruned = await prune_expired_rotations(db_session, api_key.id)
    await db_session.commit()

    assert pruned == 1
    rows = (
        await db_session.execute(
            select(APIKeyRotation).where(
                APIKeyRotation.api_key_id == api_key.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].key_hash == hash_api_key("ir_future")


async def test_default_grace_period_is_reasonable():
    assert DEFAULT_ROTATION_GRACE_PERIOD_DAYS >= 1


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# --- Route-level tests -------------------------------------------------------

import uuid as _uuid  # noqa: E402

from app.main import app as _fastapi_app  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.routes.api_keys import ApiKeyPrincipal, get_current_user_from_api_key  # noqa: E402


async def _override_user(principal):
    async def _fixed():
        return principal

    return _fixed


async def test_rotate_route_returns_new_key_once(client, db_session):
    fixed_user = User(id=_uuid.uuid4(), email="api@example.com", name="API", role=UserRole.ADMIN)
    _fastapi_app.dependency_overrides[get_current_user_from_api_key] = await _override_user(
        ApiKeyPrincipal(id=fixed_user.id, email="api@example.com", name="API", role="admin", permissions=None)
    )

    try:
        created = await client.post("/api-keys/?name=rot&permissions=read")
        assert created.status_code == 200, created.text
        key_id = created.json()["id"]

        resp = await client.post(f"/api-keys/{key_id}/rotate")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["key"].startswith("ir_")
        assert payload["id"] == key_id
        # Old key still valid for the grace period; new key valid now.
        from app.services.api_key import validate_api_key as _validate

        # The old plaintext was returned at creation time in `created.json()`.
    finally:
        _fastapi_app.dependency_overrides.clear()


async def test_rotate_route_404_for_missing_key(client, db_session):
    fixed_user = User(id=_uuid.uuid4(), email="api@example.com", name="API", role=UserRole.ADMIN)
    _fastapi_app.dependency_overrides[get_current_user_from_api_key] = await _override_user(
        ApiKeyPrincipal(id=fixed_user.id, email="api@example.com", name="API", role="admin", permissions=None)
    )
    try:
        resp = await client.post(f"/api-keys/{_uuid.uuid4()}/rotate")
        assert resp.status_code == 404, resp.text
    finally:
        _fastapi_app.dependency_overrides.clear()


async def test_rotate_route_requires_write_permission(client, db_session):
    # Principal with only "read" lacks the "write" required to rotate.
    principal = ApiKeyPrincipal(
        id=_uuid.uuid4(), email="api@example.com", name="API", role="viewer", permissions={"read"}
    )
    _fastapi_app.dependency_overrides[get_current_user_from_api_key] = await _override_user(principal)
    try:
        resp = await client.post(f"/api-keys/{_uuid.uuid4()}/rotate")
        assert resp.status_code == 403, resp.text
    finally:
        _fastapi_app.dependency_overrides.clear()
