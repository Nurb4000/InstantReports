from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.api_key import (
    generate_api_key,
    list_user_keys,
    revoke_api_key,
    validate_api_key,
)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
security = HTTPBearer(auto_error=False)


async def get_current_user_from_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Get current user from API key if provided."""
    if not credentials:
        # Try X-API-Key header
        api_key = request.headers.get("X-API-Key")
        if api_key:
            key_obj = await validate_api_key(db, api_key)
            if key_obj:
                # For API key auth, we return a minimal user object
                return User(id=key_obj.user_id, email="api-key", name="API Key", role="viewer")
        return None

    key_obj = await validate_api_key(db, credentials.credentials)
    if key_obj:
        return User(id=key_obj.user_id, email="api-key", name="API Key", role="viewer")
    return None


@router.get("/")
async def list_keys(
    current_user: User = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all API keys for the current user."""
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
    name: str = Query(...),
    permissions: str = Query("read"),
    expires_in_days: int | None = Query(None),
    current_user: User = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key."""
    perm_list = [p.strip() for p in permissions.split(",")]
    
    api_key = await generate_api_key(
        db=db,
        name=name,
        user_id=current_user.id,
        permissions=perm_list,
        expires_in_days=expires_in_days,
    )
    
    return {
        "id": str(api_key.id),
        "name": api_key.name,
        "key": api_key.key,  # Only returned once!
        "permissions": api_key.permissions.split(","),
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
    }


@router.delete("/{key_id}")
async def delete_key(
    key_id: uuid.UUID,
    current_user: User = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key."""
    success = await revoke_api_key(db, key_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "ok"}
