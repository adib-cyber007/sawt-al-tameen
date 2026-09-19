"""Thin WebSocket transports for AssemblyAI's browser and Twilio media bridges."""

from fastapi import APIRouter, Query, WebSocket

from preauth.api.assemblyai_bridge import (
    bridge_browser,
    bridge_twilio,
    close_after_bridge,
    twilio_call_sid,
)

router = APIRouter(prefix="/api/v1/voice/assemblyai", tags=["Voice channel (AssemblyAI)"])


@router.websocket("/browser")
async def assemblyai_browser(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    if not (
        settings.assemblyai_enabled and settings.assemblyai_api_key and settings.assemblyai_browser_agent_id
    ):
        await websocket.close(code=1008, reason="AssemblyAI browser channel is not configured")
        return
    await websocket.accept()
    await close_after_bridge(
        websocket, bridge_browser(websocket, settings, websocket.app.state.assemblyai_voice_gateway)
    )


@router.websocket("/twilio")
async def assemblyai_twilio(websocket: WebSocket, token: str = Query(default="")) -> None:
    settings = websocket.app.state.settings
    call_sid = twilio_call_sid(settings, token)
    if not (
        settings.assemblyai_enabled
        and settings.assemblyai_api_key
        and settings.assemblyai_phone_agent_id
        and call_sid
    ):
        await websocket.close(code=1008, reason="AssemblyAI Twilio channel is not authorised")
        return
    await websocket.accept()
    await close_after_bridge(
        websocket,
        bridge_twilio(websocket, settings, websocket.app.state.assemblyai_voice_gateway, call_sid),
    )
