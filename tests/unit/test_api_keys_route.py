"""Route-level test for the API-key create endpoint.

Closes the loop on the cleartext-storage fix: the endpoint must return the
plaintext key to the caller while the database persists only its hash.
"""

from __future__ import annotations

import uuid

from app.main import app as fastapi_app
from app.models.user import User, UserRole
from app.routes.api_keys import get_current_user_from_api_key


async def test_create_key_returns_plaintext_stores_hash(client, db_session):
    fake_user = User(
        id=uuid.uuid4(),
        email="api@example.com",
        name="API",
        role=UserRole.VIEWER,
    )

    async def _fixed_user():
        return fake_user

    fastapi_app.dependency_overrides[get_current_user_from_api_key] = _fixed_user

    resp = await client.post("/api-keys/?name=test&permissions=read,execute")
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    plaintext = payload["key"]
    assert plaintext.startswith("ir_")

    # The stored row must hold the hash, never the plaintext.
    from sqlalchemy import select

    from app.models.api_key import APIKey
    from app.services.api_key import hash_api_key

    row = (await db_session.execute(
        select(APIKey).where(APIKey.name == "test")
    )).scalar_one()
    assert row.key == hash_api_key(plaintext)
    assert row.key != plaintext
    # Permissions are still echoed back to the caller as before.
    assert payload["permissions"] == ["read", "execute"]
