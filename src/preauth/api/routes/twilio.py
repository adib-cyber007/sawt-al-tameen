"""Transport for inbound calls on our own Twilio number. Thin: see the AssemblyAI media service."""

from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

from preauth.api.errors import ErrorResponse
from preauth.api.routes.cases import _doc
from preauth.application.twilio_inbound_service import INBOUND_PATH, TwilioInboundService

router = APIRouter(tags=["Voice channel (Twilio inbound)"])


@router.post(
    INBOUND_PATH,
    summary="Twilio incoming-call webhook",
    description=_doc(
        "Set as the Voice webhook of your Twilio number (A call comes in → Webhook → HTTP POST). Accepts Twilio's "
        "form-encoded call parameters and returns `application/xml` TwiML containing a signed, short-lived "
        "AssemblyAI media WebSocket URL.",
        "Header `X-Twilio-Signature`, validated with `TWILIO_AUTH_TOKEN` against `PREAUTH_PUBLIC_BASE_URL` + this "
        "path. Provider-specific credentials and agent IDs must also be configured.",
        "None. The call uses the AssemblyAI business tools and transcript safety boundary.",
    ),
    responses={
        200: {"content": {"application/xml": {}}, "description": "TwiML for Twilio"},
        400: {"model": ErrorResponse, "description": "TWILIO_CALL_INVALID (From or To missing)"},
        401: {"model": ErrorResponse, "description": "TWILIO_SIGNATURE_INVALID"},
        503: {"model": ErrorResponse, "description": "CHANNEL_NOT_CONFIGURED"},
    },
    response_class=Response,
)
async def twilio_inbound(
    request: Request,
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
) -> Response:
    service: TwilioInboundService = request.app.state.twilio_inbound
    raw = await request.body()
    result = service.handle(raw, x_twilio_signature, request.url.query)
    return Response(content=result.twiml, media_type="application/xml")
