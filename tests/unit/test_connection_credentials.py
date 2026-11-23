"""Tests for connection credential rotation with a grace period (Gap #11).

Covers retaining the retired value, re-exposing it during grace, falling back
through ``connector_secret``, ownership enforcement, and pruning after grace.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models.connection import ConnectionCredentialRotation, DataConnection
from app.models.user import User, UserRole
from app.services.connection_credentials import (
    DEFAULT_CREDENTIAL_GRACE_PERIOD_DAYS,
    connector_secret,
    hash_secret,
    prune_expired_connection_rotations,
    resolve_connection_config,
    rotate_connection_secret,
)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


async def _make_connection(db, owner_id=None, config=None):
    """Create a connection owned by ``owner_id`` (default new user)."""
    if owner_id is None:
        owner_id = _uid()
    user = User(
        id=owner_id,
        email=f"user_{uuid.uuid4().hex[:8]}@example.com",
        name="Test",
        role=UserRole.DESIGNER,
    )
    db.add(user)
    config = config if config is not None else {"host": "h", "password": "old"}
    connection = DataConnection(
        id=_uid(),
        name="conn",
        connector_type="postgresql",
        config=config,
        created_by=owner_id,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection, owner_id


async def test_rotate_retains_old_value_and_updates_config(db_session):
    connection, owner = await _make_connection(db_session)

    rotated = await rotate_connection_secret(
        db_session, connection.id, owner, "password", "new", grace_period_days=7
    )

    # Config now holds the new secret.
    assert rotated.config["password"] == "new"

    # A grace entry was recorded for the retired value.
    row = (
        await db_session.execute(
            select(ConnectionCredentialRotation).where(
                ConnectionCredentialRotation.connection_id == connection.id
            )
        )
    ).scalar_one()
    assert row.field_name == "password"
    assert row.secret_value == "old"
    assert row.secret_hash == hash_secret("old")
    assert row.valid_until > _future()


async def test_resolve_returns_current_and_previous_during_grace(db_session):
    connection, owner = await _make_connection(db_session)
    await rotate_connection_secret(
        db_session, connection.id, owner, "password", "new", grace_period_days=7
    )

    resolved = await resolve_connection_config(db_session, connection)

    # Current secret is present and unchanged.
    assert resolved["password"] == "new"
    # Retired secret is re-exposed under the _previous_ key during grace.
    assert resolved["_previous_password"] == "old"


async def test_previous_pruned_after_grace_expires(db_session):
    connection, owner = await _make_connection(db_session)
    await rotate_connection_secret(
        db_session, connection.id, owner, "password", "new", grace_period_days=7
    )

    # Force the grace entry into the past.
    row = (
        await db_session.execute(
            select(ConnectionCredentialRotation).where(
                ConnectionCredentialRotation.connection_id == connection.id
            )
        )
    ).scalar_one()
    row.valid_until = _past()
    await db_session.commit()

    resolved = await resolve_connection_config(db_session, connection)

    # Expired previous value is pruned -> no fallback key.
    assert "_previous_password" not in resolved
    rows = (
        await db_session.execute(
            select(ConnectionCredentialRotation).where(
                ConnectionCredentialRotation.connection_id == connection.id
            )
        )
    ).scalars().all()
    assert len(rows) == 0


async def test_connector_secret_prefers_current_then_previous(db_session):
    # Current present -> returns current even if a previous exists.
    cfg = {"password": "current", "_previous_password": "old"}
    assert connector_secret(cfg, "password") == "current"

    # Current absent -> falls back to previous.
    cfg2 = {"_previous_password": "old"}
    assert connector_secret(cfg2, "password") == "old"

    # Neither present -> default.
    assert connector_secret({}, "password", "fallback") == "fallback"


async def test_rotate_requires_ownership(db_session):
    connection, _owner = await _make_connection(db_session)
    other = _uid()

    try:
        await rotate_connection_secret(
            db_session, connection.id, other, "password", "new", grace_period_days=7
        )
    except KeyError:
        pass
    else:
        raise AssertionError("rotation should fail for a non-owner")


async def test_rotate_rejects_invalid_grace_period(db_session):
    connection, owner = await _make_connection(db_session)
    try:
        await rotate_connection_secret(
            db_session, connection.id, owner, "password", "new", grace_period_days=0
        )
    except ValueError:
        pass
    else:
        raise AssertionError("grace_period_days must be >= 1")


async def test_rotate_no_grace_when_field_absent(db_session):
    connection, owner = await _make_connection(
        db_session, config={"host": "h"}  # no password yet
    )
    await rotate_connection_secret(
        db_session, connection.id, owner, "password", "first", grace_period_days=7
    )

    # Nothing retired yet, so no rotation row is created.
    rows = (
        await db_session.execute(
            select(ConnectionCredentialRotation).where(
                ConnectionCredentialRotation.connection_id == connection.id
            )
        )
    ).scalars().all()
    assert len(rows) == 0
    assert connection.config["password"] == "first"


async def test_default_grace_period_is_reasonable():
    assert DEFAULT_CREDENTIAL_GRACE_PERIOD_DAYS >= 1


def _future():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc) + timedelta(days=1)


def _past():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc) - timedelta(days=1)


# --- Route-level tests -------------------------------------------------------

import uuid as _uuid  # noqa: E402

from app.main import app as _fastapi_app  # noqa: E402
from app.routes.auth import get_current_user_optional  # noqa: E402
from app.routes.datasources import rotate_connection_secret_endpoint  # noqa: E402
from app.models.user import User as _User, UserRole as _UserRole  # noqa: E402


async def test_rotate_secret_route_returns_redacted_config(client, db_session):
    owner_id = _uuid.uuid4()
    _fastapi_app.dependency_overrides[get_current_user_optional] = _rotate_user_override(owner_id)

    connection, _ = await _make_connection(db_session, owner_id=owner_id)
    try:
        resp = await client.post(
            f"/datasources/{connection.id}/rotate-secret",
            json={"field_name": "password", "secret": "brand-new", "grace_period_days": 5},
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()

        assert payload["field_name"] == "password"
        assert payload["grace_period_days"] == 5
        assert payload["old_key_valid_until"] is not None
        # Config is redacted -- no plaintext secret leaks back to the caller.
        assert payload["config"]["password"] == "***"
        assert "brand-new" not in str(payload["config"])

        # The DB row actually holds the new secret.
        from sqlalchemy import select

        from app.models.connection import DataConnection

        row = (await db_session.execute(
            select(DataConnection).where(DataConnection.id == connection.id)
        )).scalar_one()
        assert row.config["password"] == "brand-new"
    finally:
        _fastapi_app.dependency_overrides.clear()


async def test_rotate_secret_route_404_for_missing_connection(client, db_session):
    _fastapi_app.dependency_overrides[get_current_user_optional] = _rotate_user_override(_uuid.uuid4())
    try:
        resp = await client.post(
            f"/datasources/{_uuid.uuid4()}/rotate-secret",
            json={"field_name": "password", "secret": "x"},
        )
        assert resp.status_code == 404, resp.text
    finally:
        _fastapi_app.dependency_overrides.clear()


async def test_rotate_secret_route_400_for_bad_grace(client, db_session):
    owner_id = _uuid.uuid4()
    _fastapi_app.dependency_overrides[get_current_user_optional] = _rotate_user_override(owner_id)
    connection, _ = await _make_connection(db_session, owner_id=owner_id)
    try:
        resp = await client.post(
            f"/datasources/{connection.id}/rotate-secret",
            json={"field_name": "password", "secret": "x", "grace_period_days": 999},
        )
        assert resp.status_code == 400, resp.text
    finally:
        _fastapi_app.dependency_overrides.clear()


async def test_rotate_secret_route_requires_designer(client, db_session):
    _fastapi_app.dependency_overrides[get_current_user_optional] = lambda: None
    try:
        resp = await client.post(
            f"/datasources/{_uuid.uuid4()}/rotate-secret",
            json={"field_name": "password", "secret": "x"},
        )
        assert resp.status_code == 401, resp.text
    finally:
        _fastapi_app.dependency_overrides.clear()


def _rotate_user_override(owner_id):
    """Build an async-callable that returns a designer owning ``owner_id``."""

    async def _fixed():
        return _User(
            id=owner_id,
            email="designer@example.com",
            name="Designer",
            role=_UserRole.DESIGNER,
        )

    return _fixed
