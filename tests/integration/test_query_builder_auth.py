"""Integration tests for query-builder template authorization.

Covers round-8d: template list/get/export/delete previously operated on any
template by ID with no ownership check, so any authenticated user could read or
delete other users' saved query templates (their query_config holds raw SQL).
Templates are now scoped to creator + admins, matching the report/version model.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.models.connection import DataConnection, QueryTemplate
from app.models.user import AuthSource, User, UserRole


async def _make_user(db, email: str, role: UserRole) -> User:
    user = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("pw"), role=role,
        auth_source=AuthSource.LOCAL, is_active=True,
    )
    db.add(user)
    await db.commit()
    return user


async def _login(client, email: str):
    resp = await client.post(
        "/auth/login",
        data={"email": email, "password": "pw"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.headers["Cookie"] = f"access_token={resp.cookies.get('access_token')}"
    return client


async def _seed_template(db, connection, name="Other Template"):
    template = QueryTemplate(
        name=name, description="d", connection_id=connection.id,
        query_config={"from_tables": ["orders"]}, created_by=connection.created_by,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


async def _seed_history(db, connection, user_id, label="Secret History"):
    from app.models.connection import QueryHistory

    snapshot = QueryHistory(
        connection_id=connection.id,
        query_config={"from_tables": ["orders"]},
        label=label,
        created_by=user_id,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


@pytest.mark.asyncio
async def test_non_owner_cannot_read_or_delete_others_template(client, db_session):
    owner = await _make_user(db_session, "owner@qb.test", UserRole.DESIGNER)
    await _make_user(db_session, "other@qb.test", UserRole.DESIGNER)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()
    template = await _seed_template(db_session, conn)

    other_client = await _login(client, "other@qb.test")

    # Read (full query_config leak) and destructive delete are both blocked.
    assert (await other_client.get(f"/api/query-builder/templates/{template.id}")).status_code == 403
    assert (await other_client.delete(f"/api/query-builder/templates/{template.id}")).status_code == 403
    # Export of someone else's template is blocked too.
    assert (await other_client.get(f"/api/query-builder/templates/export?ids={template.id}")).status_code == 403


@pytest.mark.asyncio
async def test_owner_can_access_own_template(client, db_session):
    owner = await _make_user(db_session, "owner2@qb.test", UserRole.DESIGNER)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()
    template = await _seed_template(db_session, conn)

    owner_client = await _login(client, "owner2@qb.test")
    assert (await owner_client.get(f"/api/query-builder/templates/{template.id}")).status_code == 200
    assert (await owner_client.delete(f"/api/query-builder/templates/{template.id}")).status_code == 200


@pytest.mark.asyncio
async def test_admin_can_access_any_template(client, db_session):
    owner = await _make_user(db_session, "owner3@qb.test", UserRole.DESIGNER)
    await _make_user(db_session, "admin@qb.test", UserRole.ADMIN)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()
    template = await _seed_template(db_session, conn)

    admin_client = await _login(client, "admin@qb.test")
    assert (await admin_client.get(f"/api/query-builder/templates/{template.id}")).status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_use_query_builder(client, db_session):
    """A viewer session must not introspect schemas or run raw SQL via the
    query-builder API. It previously required only an authenticated session, so
    any user could execute arbitrary SQL against any connection."""
    owner = await _make_user(db_session, "owner5@qb.test", UserRole.DESIGNER)
    await _make_user(db_session, "viewer@qb.test", UserRole.VIEWER)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()

    viewer_client = await _login(client, "viewer@qb.test")

    assert (
        await viewer_client.get(f"/api/query-builder/schema/{conn.id}")
    ).status_code == 403
    assert (
        await viewer_client.post(
            "/api/query-builder/test",
            params={"connection_id": str(conn.id)},
            json={"from_tables": ["orders"]},
        )
    ).status_code == 403
    assert (await viewer_client.get("/api/query-builder/templates")).status_code == 403


@pytest.mark.asyncio
async def test_list_scopes_away_others_templates(client, db_session):
    owner = await _make_user(db_session, "owner4@qb.test", UserRole.DESIGNER)
    await _make_user(db_session, "other2@qb.test", UserRole.DESIGNER)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()
    await _seed_template(db_session, conn, name="Secret Template")

    other_client = await _login(client, "other2@qb.test")
    listing = await other_client.get("/api/query-builder/templates")
    assert listing.status_code == 200
    names = [t["name"] for t in listing.json()]
    assert "Secret Template" not in names


@pytest.mark.asyncio
async def test_non_owner_cannot_read_or_delete_others_history(client, db_session):
    """Query-history snapshots hold raw SQL, so they must be scoped to their
    creator. Previously any user could read the full query_config or delete
    another user's snapshot by id."""
    owner = await _make_user(db_session, "howner@qb.test", UserRole.DESIGNER)
    await _make_user(db_session, "hother@qb.test", UserRole.DESIGNER)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()
    snapshot = await _seed_history(db_session, conn, owner.id)

    other_client = await _login(client, "hother@qb.test")

    assert (
        await other_client.get(f"/api/query-builder/history/{snapshot.id}")
    ).status_code == 403
    assert (
        await other_client.delete(f"/api/query-builder/history/{snapshot.id}")
    ).status_code == 403
    listing = await other_client.get("/api/query-builder/history")
    assert listing.status_code == 200
    assert str(snapshot.id) not in [h["id"] for h in listing.json()]


@pytest.mark.asyncio
async def test_owner_and_admin_can_access_history(client, db_session):
    owner = await _make_user(db_session, "howner2@qb.test", UserRole.DESIGNER)
    await _make_user(db_session, "hadmin@qb.test", UserRole.ADMIN)
    conn = DataConnection(
        id=uuid.uuid4(), name="Conn", connector_type="postgresql",
        config={}, created_by=owner.id,
    )
    db_session.add(conn)
    await db_session.commit()
    snapshot = await _seed_history(db_session, conn, owner.id)

    owner_client = await _login(client, "howner2@qb.test")
    assert (
        await owner_client.get(f"/api/query-builder/history/{snapshot.id}")
    ).status_code == 200
    assert (
        await owner_client.delete(f"/api/query-builder/history/{snapshot.id}")
    ).status_code == 200
