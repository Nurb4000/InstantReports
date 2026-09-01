"""Integration tests for template export/import endpoints."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import DataConnection, QueryTemplate
from app.models.user import AuthSource, User, UserRole


async def _seed_connection(db: AsyncSession) -> DataConnection:
    user = User(
        id=uuid.uuid4(),
        email="conn@example.com",
        name="Conn Owner",
        password_hash="dummy",
        role=UserRole.ADMIN,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )
    db.add(user)
    await db.commit()

    connection = DataConnection(
        id=uuid.uuid4(),
        name="Test Conn",
        connector_type="postgresql",
        config={},
        created_by=user.id,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection


async def _seed_template(db: AsyncSession, connection: DataConnection) -> QueryTemplate:
    template = QueryTemplate(
        name="Exported",
        description="d",
        connection_id=connection.id,
        query_config={"select": [], "from_tables": ["orders"]},
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


async def test_export_templates(client, db_session):
    connection = await _seed_connection(db_session)
    template = await _seed_template(db_session, connection)

    resp = await client.get(
        f"/api/query-builder/templates/export?ids={template.id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "1.0"
    assert len(body["templates"]) == 1
    assert body["templates"][0]["name"] == "Exported"


async def test_export_multiple_templates(client, db_session):
    connection = await _seed_connection(db_session)
    t1 = await _seed_template(db_session, connection)
    t2 = await _seed_template(db_session, connection)

    resp = await client.get(
        f"/api/query-builder/templates/export?ids={t1.id},{t2.id}"
    )
    assert resp.status_code == 200
    assert len(resp.json()["templates"]) == 2


async def test_export_missing_template(client, db_session):
    missing = uuid.uuid4()
    resp = await client.get(f"/api/query-builder/templates/export?ids={missing}")
    assert resp.status_code == 404


async def test_export_invalid_id(client, db_session):
    resp = await client.get("/api/query-builder/templates/export?ids=not-a-uuid")
    assert resp.status_code == 400


async def test_import_templates(client, db_session):
    connection = await _seed_connection(db_session)
    bundle = {
        "version": "1.0",
        "templates": [{"name": "Imported", "query_config": {"from_tables": ["t"]}}],
    }
    resp = await client.post(
        f"/api/query-builder/templates/import?connection_id={connection.id}",
        json=bundle,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert len(body["templates"]) == 1

    result = await db_session.execute(select(QueryTemplate))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "Imported"
    assert rows[0].connection_id == connection.id


async def test_import_rebinds_to_connection(client, db_session):
    source = await _seed_connection(db_session)
    importer = await _seed_connection(db_session)
    assert source.id != importer.id

    bundle = {
        "templates": [
            {
                "name": "Relpoint",
                "connection_id": str(source.id),
                "query_config": {"from_tables": ["t"]},
            }
        ]
    }
    resp = await client.post(
        f"/api/query-builder/templates/import?connection_id={importer.id}",
        json=bundle,
    )
    assert resp.status_code == 200
    result = await db_session.execute(select(QueryTemplate))
    row = result.scalars().one()
    # Rebound to the importer's connection, not the source's.
    assert row.connection_id == importer.id


async def test_import_invalid_connection(client, db_session):
    resp = await client.post(
        "/api/query-builder/templates/import?connection_id=not-a-uuid",
        json={"templates": [{"query_config": {}}]},
    )
    assert resp.status_code == 400


async def test_import_missing_connection(client, db_session):
    resp = await client.post(
        f"/api/query-builder/templates/import?connection_id={uuid.uuid4()}",
        json={"templates": [{"query_config": {}}]},
    )
    assert resp.status_code == 404


async def test_import_malformed_payload(client, db_session):
    connection = await _seed_connection(db_session)
    resp = await client.post(
        f"/api/query-builder/templates/import?connection_id={connection.id}",
        json={"templates": [{"name": "no config"}]},
    )
    assert resp.status_code == 400
