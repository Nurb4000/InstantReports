"""Tests for portal report history endpoint."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_report_history_requires_auth(client: AsyncClient):
    """Unauthenticated requests to /reports/{id}/history should redirect."""
    response = await client.get(f"/portal/reports/{uuid.uuid4()}/history")
    assert response.status_code in (307, 401)


@pytest.mark.asyncio
async def test_report_history_returns_404_for_missing_report(auth_client: AsyncClient):
    """Requesting history for a non-existent report should return 404."""
    response = await auth_client.get(f"/portal/reports/{uuid.uuid4()}/history")
    assert response.status_code == 404
