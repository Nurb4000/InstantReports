from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.api_key import (
    DEFAULT_ROTATION_GRACE_PERIOD_DAYS,
    generate_api_key,
    list_user_keys,
    prune_expired_rotations,
    rotate_api_key,
    revoke_api_key,
    validate_api_key,
)
from app.routes.rate_limiting import rate_limit_api_key_endpoint

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
security = HTTPBearer(auto_error=False)

# Allow-list of permissions a key may be created with. Anything outside this set
# is rejected at creation (Gap #2 / H1) so a caller cannot mint a key with an
# arbitrary/unknown permission that later behaves unexpectedly.
_VALID_PERMISSIONS = frozenset({"read", "write", "execute", "admin"})


@dataclass(frozen=True)
class ApiKeyPrincipal:
    """Identity resolved from an API key.

    Carries the key's granted ``permissions`` (parsed from the stored,
    comma-separated list) so routes can enforce them. ``permissions`` is
    ``None`` only for synthetic principals created by tests that override the
    auth dependency with a plain ``User``; such principals are treated as
    fully authorised to preserve backward-compatible behaviour.
    """

    id: uuid.UUID
    email: str
    name: str
    role: str
    permissions: set[str] | None = None


def _parse_permissions(stored: str | None) -> tuple[set[str], str]:
    """Return (permission set, derived role) from a stored permission string.

    Permissions are stored comma-separated (e.g. ``"read,write,admin"``). The
    derived role reflects the highest-granted capability so downstream
    ``check_role``-style guards stay consistent.
    """
    perms = {p.strip().lower() for p in (stored or "").split(",") if p.strip()}
    if "admin" in perms:
        role = "admin"
    elif {"write", "execute"} & perms:
        role = "designer"
    else:
        role = "viewer"
    return perms, role


async def get_current_user_from_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyPrincipal | None:
    """Resolve the calling user from an API key (Bearer or ``X-API-Key``).

    The key's stored permissions are parsed and attached to the returned
    principal so ``require_permission`` can enforce them per action.
    """
    key_obj = None
    if credentials:
        key_obj = await validate_api_key(db, credentials.credentials)
    else:
        api_key = request.headers.get("X-API-Key")
        if api_key:
            key_obj = await validate_api_key(db, api_key)

    if not key_obj:
        if credentials or request.headers.get("X-API-Key"):
            raise HTTPException(status_code=401, detail="Missing/invalid API key")
        return None

    perms, role = _parse_permissions(key_obj.permissions)
    return ApiKeyPrincipal(
        id=key_obj.user_id,
        email="api-key",
        name=key_obj.name,
        role=role,
        permissions=perms,
    )


def require_permission(permission: str):
    """Return a dependency enforcing that the API key grants ``permission``.

    Factory form so it can be used as ``Depends(require_permission("write"))``.
    Keys with an explicit permission set are enforced strictly; synthetic
    principals without one (e.g. test overrides returning a plain ``User``)
    are allowed through to preserve existing behaviour.
    """

    async def _check(
        principal: ApiKeyPrincipal | None = Depends(get_current_user_from_api_key),
    ) -> None:
        if principal is None:
            raise HTTPException(status_code=401, detail="Missing/invalid API key")
        perms = getattr(principal, "permissions", None)
        if perms is not None and permission.strip().lower() not in {
            p.strip().lower() for p in perms
        }:
            raise HTTPException(
                status_code=403,
                detail=f"API key lacks '{permission}' permission",
            )

    return _check


@router.get("/")
async def list_keys(
    api_quota: None = Depends(rate_limit_api_key_endpoint()),
    _: None = Depends(require_permission("read")),
    current_user: ApiKeyPrincipal = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all API keys for the current user (requires ``read``)."""
    keys = await list_user_keys(db, current_user.id)
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "permissions": k.permissions.split(","),
            "is_active": k.is_active,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


@router.post("/")
async def create_key(
    api_quota: None = Depends(rate_limit_api_key_endpoint()),
    name: str = Query(...),
    permissions: str = Query("read"),
    expires_in_days: int | None = Query(None),
    _: None = Depends(require_permission("write")),
    current_user: ApiKeyPrincipal = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key (requires ``write``)."""
    # Gap #2 / H1: validate requested permissions against an allow-list instead
    # of accepting an arbitrary string. Normalise to lowercase; enforcement is
    # already case-insensitive, so this also stores a canonical value.
    perm_list = [p.strip().lower() for p in permissions.split(",") if p.strip()]
    if not perm_list:
        raise HTTPException(
            status_code=400, detail="At least one permission is required"
        )
    invalid = {p for p in perm_list if p not in _VALID_PERMISSIONS}
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid permission(s): {', '.join(sorted(invalid))}. "
                "Valid permissions: read, write, execute, admin."
            ),
        )

    api_key, raw_key = await generate_api_key(
        db=db,
        name=name,
        user_id=current_user.id,
        permissions=perm_list,
        expires_in_days=expires_in_days,
    )

    return {
        "id": str(api_key.id),
        "name": api_key.name,
        "key": raw_key,  # Only returned once — the DB stores a hash, not this.
        "permissions": api_key.permissions.split(","),
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
    }


@router.delete("/{key_id}")
async def delete_key(
    key_id: uuid.UUID,
    api_quota: None = Depends(rate_limit_api_key_endpoint()),
    _: None = Depends(require_permission("admin")),
    current_user: ApiKeyPrincipal = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key (requires ``admin``)."""
    success = await revoke_api_key(db, key_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "ok"}


@router.post("/{key_id}/rotate")
async def rotate_key(
    key_id: uuid.UUID,
    api_quota: None = Depends(rate_limit_api_key_endpoint()),
    grace_period_days: int | None = Query(None, description="Days the retired key stays valid (1-30)"),
    _: None = Depends(require_permission("write")),
    current_user: ApiKeyPrincipal = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Rotate an API key (requires ``write``).

    A new plaintext key is returned once; the previous key remains valid for the
    grace period (default 7 days, max 30) so existing clients keep working until
    it elapses. Expired rotations are pruned as part of the operation.
    """
    try:
        api_key, new_key = await rotate_api_key(
            db=db,
            key_id=key_id,
            user_id=current_user.id,
            grace_period_days=(
                grace_period_days
                if grace_period_days is not None
                else DEFAULT_ROTATION_GRACE_PERIOD_DAYS
            ),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="API key not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Report when the retired key stops working so clients know their deadline.
    from app.models.api_key import APIKeyRotation

    result = await db.execute(
        select(APIKeyRotation)
        .where(APIKeyRotation.api_key_id == api_key.id)
        .order_by(APIKeyRotation.created_at.desc())
        .limit(1)
    )
    rotation = result.scalar_one_or_none()
    old_key_valid_until = rotation.valid_until.isoformat() if rotation else None

    await prune_expired_rotations(db, api_key.id)
    await db.commit()

    return {
        "id": str(api_key.id),
        "name": api_key.name,
        "key": new_key,  # Only returned once -- the DB stores a hash, not this.
        "permissions": api_key.permissions.split(","),
        "grace_period_days": (
            grace_period_days
            if grace_period_days is not None
            else DEFAULT_ROTATION_GRACE_PERIOD_DAYS
        ),
        "old_key_valid_until": old_key_valid_until,
    }
