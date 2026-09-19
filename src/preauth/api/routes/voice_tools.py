"""Provider-neutral diagnostic transport for the voice-agent toolbox."""

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Path, Request

from preauth.agent_tools.voice_gateway import VoiceToolGateway, VoiceToolResponse
from preauth.api.actor import ActorRequiredError
from preauth.api.errors import ErrorResponse
from preauth.api.routes.cases import _doc
from preauth.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/voice", tags=["Voice channel diagnostics"])


class ChannelNotConfiguredError(DomainError):
    code = "CHANNEL_NOT_CONFIGURED"


def _require_token(request: Request, authorization: str | None) -> None:
    expected = request.app.state.settings.voice_tool_token
    if expected is None:
        raise ChannelNotConfiguredError("Voice-tool diagnostics are disabled")
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise ActorRequiredError("Missing or invalid voice-tool token", code="VOICE_TOKEN_INVALID")


@router.post(
    "/tools/{tool_name}",
    summary="Invoke a voice tool for deployment diagnostics",
    description=_doc(
        "Invokes one provider-neutral voice tool and links the optional `X-Conversation-ID` to any case it touches. "
        "This endpoint is used by deployment verification; live AssemblyAI sessions execute the same gateway "
        "directly over their server-owned WebSocket.",
        "Header `Authorization: Bearer <PREAUTH_VOICE_TOOL_TOKEN>`.",
        "Those of the underlying tool. Never `APPROVED` or `DENIED`.",
    ),
    responses={
        401: {"model": ErrorResponse, "description": "VOICE_TOKEN_INVALID"},
        503: {"model": ErrorResponse, "description": "CHANNEL_NOT_CONFIGURED"},
        500: {"model": ErrorResponse, "description": "INTERNAL_ERROR"},
    },
)
def voice_tool(
    request: Request,
    tool_name: Annotated[str, Path(pattern=r"^[a-z_]{1,64}$")],
    arguments: Annotated[dict[str, Any], Body(default_factory=dict)],
    authorization: Annotated[str | None, Header()] = None,
    x_conversation_id: Annotated[str | None, Header(description="Diagnostic conversation id")] = None,
) -> VoiceToolResponse:
    _require_token(request, authorization)
    gateway: VoiceToolGateway = request.app.state.voice_gateway
    return gateway.call(tool_name, arguments, conversation_id=x_conversation_id)
