"""Tests for image-upload hardening (M3).

Covers the fix: uploads previously trusted the client Content-Type and derived
the saved extension from the user-controlled filename, so ``evil.html``/``evil.svg``
(with ``Content-Type: image/svg+xml``) could be stored under ``static/img`` and
served same-origin (stored XSS). There was also no size cap (disk-fill DoS).
The route now sniffs magic bytes, allows only png/jpg/gif/webp, and enforces a
size limit.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.main import app as fastapi_app
from app.models.user import AuthSource, User, UserRole
from app.routes.auth import get_current_user_optional

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="designer@upload.test",
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


@pytest.mark.asyncio
async def test_html_disguised_as_image_rejected(client):
    _override_user(_user())

    resp = await client.post(
        "/designer/images/upload",
        files={"file": ("evil.html", b"<script>alert(1)</script>", "image/png")},
    )
    assert resp.status_code == 400
    assert "Unsupported image format" in resp.text


@pytest.mark.asyncio
async def test_svg_disguised_as_image_rejected(client):
    _override_user(_user())

    resp = await client.post(
        "/designer/images/upload",
        files={"file": ("evil.svg", b"<svg onload=alert(1)/>", "image/svg+xml")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_valid_png_accepted_with_trusted_extension(client, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STATIC_DIR", tmp_path)
    _override_user(_user())

    resp = await client.post(
        "/designer/images/upload",
        files={"file": ("whatever.txt", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"].endswith(".png")
    assert body["url"].startswith("/static/img/")
    assert (tmp_path / "img" / body["filename"]).read_bytes() == PNG_BYTES


@pytest.mark.asyncio
async def test_oversized_upload_rejected(client):
    _override_user(_user())
    from app.config import settings

    oversized = PNG_BYTES + b"\x00" * (settings.MAX_IMAGE_UPLOAD_BYTES + 1)

    resp = await client.post(
        "/designer/images/upload",
        files={"file": ("big.png", oversized, "image/png")},
    )
    assert resp.status_code == 413
