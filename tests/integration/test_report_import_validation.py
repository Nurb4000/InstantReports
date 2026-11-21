"""End-to-end tests for report-import schema validation (Gap #4).

Proves the import route rejects malformed/hostile definitions with a 400 before
persisting them, and accepts well-formed ones. Complements the pure-function
tests in test_definition_validation.py by exercising the route boundary.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional


def _designer() -> User:
    return User(
        id=uuid.uuid4(),
        email="designer@import.test",
        name="Designer",
        password_hash=hash_password("pw"),
        role=UserRole.DESIGNER,
        auth_source=AuthSource.LOCAL,
        is_active=True,
    )


def _override_user(user: User) -> None:
    async def _dep():
        return user

    fastapi_app.dependency_overrides[get_current_user_optional] = _dep


def _export_payload(definition: dict) -> bytes:
    return json.dumps(
        {
            "instantreports_export": True,
            "version": "1.0",
            "report": {"name": "Imported", "definition": definition},
        }
    ).encode()


VALID_DEFINITION = {
    "layout": {"sections": [{"type": "header"}, {"type": "detail", "elements": []}]},
    "data_sources": [],
    "parameters": [],
}


@pytest.mark.asyncio
async def test_valid_definition_imported(client):
    _override_user(_designer())
    resp = await client.post(
        "/designer/reports/import",
        files={"file": ("report.json", _export_payload(VALID_DEFINITION), "application/json")},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_too_many_sections_rejected(client):
    _override_user(_designer())
    sections = [{"type": "detail", "elements": []} for _ in range(201)]
    payload = _export_payload({"layout": {"sections": sections}})
    resp = await client.post(
        "/designer/reports/import",
        files={"file": ("report.json", payload, "application/json")},
    )
    assert resp.status_code == 400
    assert "Invalid report definition" in resp.text


@pytest.mark.asyncio
async def test_huge_string_rejected(client):
    _override_user(_designer())
    big = "x" * (20_000 + 1)
    payload = _export_payload(
        {"layout": {"sections": [], "name": big}}
    )
    resp = await client.post(
        "/designer/reports/import",
        files={"file": ("report.json", payload, "application/json")},
    )
    assert resp.status_code == 400
    assert "characters" in resp.text


@pytest.mark.asyncio
async def test_malformed_structure_rejected(client):
    _override_user(_designer())
    payload = _export_payload({"layout": {"sections": "not-a-list"}})
    resp = await client.post(
        "/designer/reports/import",
        files={"file": ("report.json", payload, "application/json")},
    )
    assert resp.status_code == 400
    assert "sections" in resp.text
