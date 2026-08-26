from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user_optional
from app.models.user import User

router = APIRouter()


@router.post("/generate-report")
async def generate_report(
    prompt: str,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.config import settings

    if not settings.AI_ENABLED:
        raise HTTPException(status_code=503, detail="AI is not configured")

    return {"status": "ok", "message": "AI generation not yet implemented"}


@router.post("/generate-sql")
async def generate_sql(
    prompt: str,
    schema: dict = None,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.config import settings

    if not settings.AI_ENABLED:
        raise HTTPException(status_code=503, detail="AI is not configured")

    return {"status": "ok", "message": "AI generation not yet implemented"}


@router.post("/suggest-layout")
async def suggest_layout(
    data_schema: dict,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.config import settings

    if not settings.AI_ENABLED:
        raise HTTPException(status_code=503, detail="AI is not configured")

    return {"status": "ok", "message": "AI generation not yet implemented"}


@router.post("/insights")
async def get_insights(
    data: dict,
    current_user: User | None = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.config import settings

    if not settings.AI_ENABLED:
        raise HTTPException(status_code=503, detail="AI is not configured")

    return {"status": "ok", "message": "AI generation not yet implemented"}
