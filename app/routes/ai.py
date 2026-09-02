from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.models.user import User
from app.routes.auth import get_current_user_optional
from app.services.ai.client import (
    AIClient,
    AIDataInsights,
    AILayoutAssistant,
    AIReportGenerator,
    AISQLGenerator,
)

router = APIRouter()


async def get_ai_client():
    """Get configured AI client."""
    if not settings.AI_ENABLED:
        raise HTTPException(status_code=503, detail="AI is not configured. Set AI_ENABLED=true and configure AI_BASE_URL.")

    return AIClient(
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL,
    )


@router.post("/generate-report")
async def generate_report(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Generate a report definition from natural language."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    prompt = body.get("prompt", "")
    schema = body.get("schema")

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    try:
        client = await get_ai_client()
        generator = AIReportGenerator(client)
        report_def = await generator.generate_report(prompt, schema)
        return {"status": "ok", "report_definition": report_def}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e!s}")


@router.post("/generate-sql")
async def generate_sql(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Generate SQL from natural language."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    prompt = body.get("prompt", "")
    schema = body.get("schema", {})
    connector_type = body.get("connector_type", "postgresql")

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    try:
        client = await get_ai_client()
        generator = AISQLGenerator(client)
        sql = await generator.generate_sql(prompt, schema, connector_type)
        return {"status": "ok", "sql": sql}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL generation failed: {e!s}")


@router.post("/suggest-layout")
async def suggest_layout(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Get layout suggestions from AI."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    data_schema = body.get("data_schema", {})
    report_type = body.get("report_type", "summary")

    if not data_schema:
        raise HTTPException(status_code=400, detail="Data schema is required")

    try:
        client = await get_ai_client()
        assistant = AILayoutAssistant(client)
        layout = await assistant.suggest_layout(data_schema, report_type)
        return {"status": "ok", "layout_suggestion": layout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Layout suggestion failed: {e!s}")


@router.post("/insights")
async def get_insights(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Generate insights from data."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    data = body.get("data", [])
    context = body.get("context", "")

    if not data:
        raise HTTPException(status_code=400, detail="Data is required")

    try:
        client = await get_ai_client()
        insights_gen = AIDataInsights(client)
        insights = await insights_gen.generate_insights(data, context)
        return {"status": "ok", "insights": insights}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Insights generation failed: {e!s}")


@router.post("/chat")
async def ai_chat(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """General AI chat endpoint for the designer sidebar."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    message = body.get("message", "")
    conversation_history = body.get("history", [])

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    try:
        client = await get_ai_client()

        messages = [
            {"role": "system", "content": "You are a helpful report design assistant. Help users create, modify, and understand reports."},
        ]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": message})

        response = await client.chat_completion(messages=messages, temperature=0.7)
        return {"status": "ok", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {e!s}")
