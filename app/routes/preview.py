from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.auth import get_current_user_optional
from app.models.user import User

router = APIRouter()


@router.websocket("/ws/{report_id}")
async def preview_websocket(
    websocket: WebSocket,
    report_id: str,
    current_user: User | None = Depends(get_current_user_optional),
):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        pass
