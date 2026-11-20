"""Tests for API-key permission enforcement (H1).

API keys carry a stored, comma-separated permission list (e.g. ``"read"``,
``"read,write"``, ``"read,write,admin"``). Previously that list was written but
never read, and every API-key principal was hard-coded to ``role="viewer"`` with
no per-action checks. These tests verify the stored permissions are now parsed
and enforced on the management endpoints:

* ``read``  -> list keys (GET)
* ``write`` -> create a key (POST)
* ``admin`` -> revoke a key (DELETE)
"""

from __future__ import annotations

import uuid

from app.models.api_key import APIKey
from app.services.api_key import generate_api_key, hash_api_key
from sqlalchemy import select

# A valid UUID that will never exist in the test DB; used so path-parameter
# validation passes and the request reaches the permission gate.
_NONEXISTENT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


async def _make_key(db_session, permissions: str, *, active: bool = True, expired: bool = False):
    """Create an API key with the given permission string and return its plaintext."""
    expires_in_days = -1 if expired else None
    api_key, plaintext = await generate_api_key(
        db=db_session,
        name=f"key-{permissions.replace(',', '-')}",
        user_id=uuid.uuid4(),
        permissions=[p.strip() for p in permissions.split(",")],
        expires_in_days=expires_in_days,
    )
    if not active:
        api_key.is_active = False
        await db_session.commit()
    return plaintext


def _headers(plaintext: str) -> dict:
    return {"X-API-Key": plaintext}


async def test_read_key_can_list_but_not_write_or_admin(client, db_session):
    key = await _make_key(db_session, "read")

    # read permission -> listing is allowed.
    resp = await client.get("/api-keys/", headers=_headers(key))
    assert resp.status_code == 200, resp.text

    # No write permission -> creation denied.
    resp = await client.post("/api-keys/?name=x", headers=_headers(key))
    assert resp.status_code == 403, resp.text

    # No admin permission -> deletion denied.
    resp = await client.delete(f"/api-keys/{_NONEXISTENT_ID}", headers=_headers(key))
    assert resp.status_code == 403, resp.text


async def test_write_key_can_create_but_not_admin(client, db_session):
    key = await _make_key(db_session, "read,write")

    resp = await client.post("/api-keys/?name=writer", headers=_headers(key))
    assert resp.status_code == 200, resp.text
    assert resp.json()["key"].startswith("ir_")

    # write is not enough to revoke (admin only).
    resp = await client.delete(f"/api-keys/{_NONEXISTENT_ID}", headers=_headers(key))
    assert resp.status_code == 403, resp.text


async def test_admin_key_can_do_everything(client, db_session):
    key = await _make_key(db_session, "read,write,admin")

    assert (await client.get("/api-keys/", headers=_headers(key))).status_code == 200
    assert (await client.post("/api-keys/?name=admin", headers=_headers(key))).status_code == 200
    # DELETE returns 404 for a non-existent id, which is past the permission gate.
    resp = await client.delete("/api-keys/00000000-0000-0000-0000-000000000000", headers=_headers(key))
    assert resp.status_code == 404, resp.text


async def test_missing_key_is_401(client, db_session):
    resp = await client.get("/api-keys/")
    assert resp.status_code == 401, resp.text


async def test_invalid_key_is_401(client, db_session):
    resp = await client.get("/api-keys/", headers=_headers("ir_not_a_real_key"))
    assert resp.status_code == 401, resp.text


async def test_expired_key_is_401(client, db_session):
    key = await _make_key(db_session, "read,write,admin", expired=True)
    resp = await client.get("/api-keys/", headers=_headers(key))
    assert resp.status_code == 401, resp.text


async def test_inactive_key_is_401(client, db_session):
    key = await _make_key(db_session, "read,write,admin", active=False)
    resp = await client.get("/api-keys/", headers=_headers(key))
    assert resp.status_code == 401, resp.text


async def test_permissions_are_parsed_from_stored_string(client, db_session):
    """The stored permission string is what gates access, not a hard-coded role."""
    key = await _make_key(db_session, "read")

    # Confirm the row really stores the comma list we passed in.
    row = (await db_session.execute(
        select(APIKey).where(APIKey.key == hash_api_key(key))
    )).scalar_one()
    assert row.permissions == "read"

    # read-only key is denied the write action even though it is valid.
    resp = await client.post("/api-keys/?name=x", headers=_headers(key))
    assert resp.status_code == 403, resp.text
