"""Thin transports for AssemblyAI realtime sessions and completed-session webhooks."""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, WebSocket
from starlette.concurrency import run_in_threadpool

from preauth.api.errors import ErrorResponse
from preauth.api.assemblyai_bridge import (
    bridge_browser,
    bridge_twilio,
    close_after_bridge,
    twilio_call_sid,
)
from preauth.api.routes.cases import _doc
from preauth.application.assemblyai_post_call import AssemblyAIPostCallService
from preauth.application.voice_channel_service import PostCallOutcome

router = APIRouter(prefix="/api/v1/voice/assemblyai", tags=["Voice channel (AssemblyAI)"])


@router.post(
    "/post-call",
    summary="AssemblyAI completed-session webhook",
    description=_doc(
        "Receives signed `session.completed` notifications, fetches the authoritative session timeline artifact, "
        "stores a normalized immutable call record, and links it to every case touched during that session. "
        "Returns a retryable 503 while the timeline artifact is still being prepared.",
        "Header `X-AAI-Signature` (timestamped HMAC-SHA256 with `PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET`).",
        "None. Human sign-off on affected cases becomes possible only after the transcript is recorded.",
    ),
    responses={
        400: {"model": ErrorResponse, "description": "WEBHOOK_PAYLOAD_INVALID"},
        401: {"model": ErrorResponse, "description": "WEBHOOK_SIGNATURE_INVALID"},
        503: {
            "model": ErrorResponse,
            "description": "CHANNEL_NOT_CONFIGURED / WEBHOOK_ARTIFACT_PENDING / VOICE_PROVIDER_UNAVAILABLE",
        },
    },
)
async def assemblyai_post_call(
    request: Request,
    signature: Annotated[str | None, Header(alias="X-AAI-Signature")] = None,
) -> PostCallOutcome:
    raw = await request.body()
    service: AssemblyAIPostCallService = request.app.state.assemblyai_post_call
    return await run_in_threadpool(service.handle, raw, signature)


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
