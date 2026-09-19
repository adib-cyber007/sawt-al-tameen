"""Transport for the local voice channel. Thin: parse, call one runtime method, return its payload.

These routes exist only when the process runs with ``PREAUTH_RUNTIME_MODE=local``; otherwise the router is not
mounted at all. They carry no insurance logic — the runtime they delegate to reaches the same application
services through the same agent toolbox as the AssemblyAI channel.
"""

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Path, Request

from preauth.api.actor import ActorRequiredError
from preauth.api.errors import ErrorResponse
from preauth.api.routes.cases import _doc
from preauth.local.errors import LocalModeNotEnabledError
from preauth.local.runtime import LocalRuntime

router = APIRouter(prefix="/api/v1/local", tags=["Voice channel (local)"])

ConversationId = Annotated[str, Path(description="Local conversation id", pattern=r"^local_[0-9a-f]{16}$")]
LOCAL_AUTH = (
    "None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require "
    "a matching `X-Gateway-Secret` header like every other route."
)


def _runtime(request: Request, x_gateway_secret: str | None) -> LocalRuntime:
    runtime = getattr(request.app.state, "local_runtime", None)
    if runtime is None:
        raise LocalModeNotEnabledError(
            "The local channel is not enabled in this process",
            details={"enable_it": "PREAUTH_RUNTIME_MODE=local"},
        )
    expected = request.app.state.settings.gateway_secret
    if expected is not None and not hmac.compare_digest((x_gateway_secret or "").encode(), expected.encode()):
        raise ActorRequiredError("Missing or invalid X-Gateway-Secret", code="GATEWAY_SECRET_INVALID")
    return runtime


Secret = Annotated[str | None, Header(alias="X-Gateway-Secret")]

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "GATEWAY_SECRET_INVALID"},
    404: {"model": ErrorResponse, "description": "CONVERSATION_NOT_FOUND"},
    409: {"model": ErrorResponse, "description": "CONVERSATION_CLOSED"},
    422: {"model": ErrorResponse, "description": "SPEECH_NOT_RECOGNISED"},
    503: {"model": ErrorResponse, "description": "CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING"},
}


@router.get(
    "/capabilities",
    summary="Which local model and speech engines this process is using",
    description=_doc(
        "Reports the configured local LLM, speech recogniser and speech synthesiser, so the browser knows "
        "whether to offer the microphone and whether to expect audio back.",
        LOCAL_AUTH,
        "None.",
    ),
    responses=_RESPONSES,
)
def capabilities(request: Request, x_gateway_secret: Secret = None) -> dict[str, Any]:
    return _runtime(request, x_gateway_secret).capabilities()


@router.get(
    "/diagnostics",
    summary="What is still missing before a local call can be taken",
    description=_doc(
        "The same checks as `scripts/check_local.py`, over HTTP. Each failing check carries the command that "
        "fixes it.",
        LOCAL_AUTH,
        "None.",
    ),
    responses=_RESPONSES,
)
def diagnostics(request: Request, x_gateway_secret: Secret = None) -> dict[str, Any]:
    from preauth.local.diagnostics import run_checks, summarise

    runtime = _runtime(request, x_gateway_secret)
    return summarise(run_checks(runtime.settings, request.app.state.settings.database_url))


@router.post(
    "/conversations",
    summary="Start a local call",
    description=_doc(
        "Opens a conversation and returns the agent's opening line, with audio when speech synthesis is "
        "configured. The `conversation_id` is passed to every tool call, so the case this call touches cannot "
        "be signed off until the call is finished and its transcript stored.",
        LOCAL_AUTH,
        "None.",
    ),
    responses=_RESPONSES,
)
def start_conversation(request: Request, x_gateway_secret: Secret = None) -> dict[str, Any]:
    return _runtime(request, x_gateway_secret).start()


@router.post(
    "/conversations/{conversation_id}/text",
    summary="Send a typed caller turn",
    description=_doc(
        "Runs one caller turn through the local model, which may call `verify_caller`, `check_coverage_rule` "
        "or `log_transcript`. Returns the agent's reply, the tool calls it made, and the state of the case.",
        LOCAL_AUTH,
        "Those of the tools the agent calls. Never `APPROVED` or `DENIED`.",
    ),
    responses=_RESPONSES,
)
def say(
    request: Request,
    conversation_id: ConversationId,
    body: Annotated[dict[str, Any], Body()],
    x_gateway_secret: Secret = None,
) -> dict[str, Any]:
    return _runtime(request, x_gateway_secret).say(conversation_id, str(body.get("text", "")))


@router.post(
    "/conversations/{conversation_id}/audio",
    summary="Send a spoken caller turn",
    description=_doc(
        "Transcribes the request body with the local speech recogniser, then handles the result exactly as a "
        "typed turn. The body is the raw recording (the browser posts a `webm/opus` blob straight from "
        "MediaRecorder); `wav`, `mp3` and `ogg` work too. Unintelligible or empty audio is rejected with "
        "`SPEECH_NOT_RECOGNISED` rather than guessed at.",
        LOCAL_AUTH,
        "Those of the tools the agent calls. Never `APPROVED` or `DENIED`.",
    ),
    responses=_RESPONSES,
)
async def listen(
    request: Request,
    conversation_id: ConversationId,
    x_gateway_secret: Secret = None,
) -> dict[str, Any]:
    from starlette.concurrency import run_in_threadpool

    runtime = _runtime(request, x_gateway_secret)
    payload = await request.body()
    # Recognition, generation and synthesis are all blocking CPU work; keep them off the event loop.
    return await run_in_threadpool(runtime.listen, conversation_id, payload)


@router.get(
    "/conversations/{conversation_id}",
    summary="Transcript and case state so far",
    description=_doc("The turns taken, the tools called, and the current case and human-review status.", LOCAL_AUTH, "None."),
    responses=_RESPONSES,
)
def conversation_state(
    request: Request, conversation_id: ConversationId, x_gateway_secret: Secret = None
) -> dict[str, Any]:
    return _runtime(request, x_gateway_secret).state(conversation_id)


@router.post(
    "/conversations/{conversation_id}/finish",
    summary="End the call and store its transcript",
    description=_doc(
        "Writes the conversation to the immutable `call_records` table and adds a `CALL_RECORDED` audit event "
        "to every case it touched. Until this runs, reviewers see `CALL_RECORD_PENDING` on those cases and "
        "cannot sign them off. Idempotent.",
        LOCAL_AUTH,
        "None directly; it unblocks human sign-off on the cases the call touched.",
    ),
    responses=_RESPONSES,
)
def finish(request: Request, conversation_id: ConversationId, x_gateway_secret: Secret = None) -> dict[str, Any]:
    return _runtime(request, x_gateway_secret).finish(conversation_id)
