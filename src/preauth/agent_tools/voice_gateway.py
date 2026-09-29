"""Adapts tool calls from hosted voice platforms onto the provider-neutral agent toolbox.

Responsibilities, all transport-level:
- normalise platform quirks (empty strings / nulls sent for parameters the model left unset);
- shape results so the model can act on failures (``ok: false`` with a stable error code and speaking guidance)
  instead of an opaque HTTP failure;
- record which conversation touched which case, so the transcript can be required before human sign-off.
"""

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from preauth.application.commands import VerifyCallerCommand, CoverageCheckCommand, LogTranscriptCommand

from preauth.agent_tools.toolbox import AgentToolbox, ToolArgumentsInvalidError
from preauth.application.voice_execution_service import ToolRequestConflict
from preauth.application.services import ApplicationServices
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType
from preauth.application.voice_channel_service import bind_voice_context
from preauth.domain.errors import (
    AuthorizationError,
    ConcurrencyConflictError,
    DomainError,
    InvalidStateTransitionError,
    NotFoundError,
    OperationNotAllowedError,
    ValidationFailedError,
)

logger = logging.getLogger("preauth.voice.gateway")

ASSEMBLYAI_AGENT_ACTOR = Actor(ActorType.VOICE_AGENT, "assemblyai-agent")
# The local channel is the same kind of actor with a different name: still VOICE_AGENT, so the review API
# rejects it exactly as it rejects the hosted agent.
LOCAL_AGENT_ACTOR = Actor(ActorType.VOICE_AGENT, "local-agent")
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

_GUIDANCE: list[tuple[type[DomainError], str]] = [
    (ToolArgumentsInvalidError, "A value was missing or badly formatted. Ask the caller to repeat or spell it."),
    (ValidationFailedError, "The caller's details did not match our records. Read the value back and ask the caller to confirm it."),
    (NotFoundError, "Nothing was found for that reference. Ask the caller to confirm the reference."),
    (InvalidStateTransitionError, "That step is not possible for this case right now. Check the case status before continuing."),
    (OperationNotAllowedError, "That step is not possible for this case right now. Check the case status before continuing."),
    (ConcurrencyConflictError, "The case was updated at the same moment. Retry the same tool call once."),
    (AuthorizationError, "This action is not available to the voice agent. Offer a human callback instead."),
]


class VoiceToolResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    guidance: str | None = None
    request_id: str | None = None


def _clean(arguments: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in arguments.items() if v is not None and not (isinstance(v, str) and not v.strip())}


def _case_id_of(arguments: dict[str, Any], result: dict[str, Any] | None) -> str | None:
    """The case a tool call touched: from the result when it names one, else from the arguments."""
    if result:
        if "case_reference" in result and isinstance(result.get("id"), str):  # a CaseView
            return result["id"]
        if isinstance(result.get("case_id"), str):
            return result["case_id"]
        recommendation = result.get("recommendation")
        if isinstance(recommendation, dict) and isinstance(recommendation.get("case_id"), str):
            return recommendation["case_id"]
    value = arguments.get("case_id")
    return value if isinstance(value, str) else None


class VoiceToolGateway:
    def __init__(
        self, services: ApplicationServices, toolbox: AgentToolbox, actor: Actor = ASSEMBLYAI_AGENT_ACTOR
    ):
        if actor.type is not ActorType.VOICE_AGENT:
            raise AuthorizationError(
                "The voice gateway may only act as a VOICE_AGENT actor",
                code="VOICE_AGENT_REQUIRED",
                details={"actor_type": actor.type},
            )
        self._services = services
        self._toolbox = toolbox
        self._actor = actor

    def call(
        self, tool_name: str, arguments: dict[str, Any], conversation_id: str | None = None,
        provider_call_id: str | None = None,
    ) -> VoiceToolResponse:
        valid_conversation = conversation_id if conversation_id and _CONVERSATION_ID.fullmatch(conversation_id) else None
        bind_voice_context(self._actor, valid_conversation)
        cleaned = _clean(arguments)
        if provider_call_id is not None and (
            not valid_conversation or not isinstance(provider_call_id, str) or not 1 <= len(provider_call_id) <= 100
        ):
            return VoiceToolResponse(ok=False, error={"code": "TOOL_CONTEXT_INVALID", "message": "Missing valid session or call identity"})
        if valid_conversation:
            request_key, request_id = cleaned.get("request_key"), cleaned.get("request_id")
            if any(value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 100)
                   for value in (request_key, request_id)):
                return VoiceToolResponse(ok=False, error={"code": "TOOL_ARGUMENTS_INVALID", "message": "Invalid request identity"})
            business_arguments = {k: v for k, v in cleaned.items() if k not in {"request_id", "request_key"}}
            command_model = {"verify_caller": VerifyCallerCommand, "check_coverage_rule": CoverageCheckCommand,
                             "log_transcript": LogTranscriptCommand}.get(tool_name)
            if command_model:
                try:
                    business_arguments = command_model.model_validate(business_arguments).model_dump(mode="json", exclude_none=True)
                except ValidationError:
                    pass  # Let the toolbox return its normal, durable validation error.
            try:
                response = self._services.executions.execute(
                    actor_id=self._actor.id, conversation_id=valid_conversation,
                    provider_call_id=provider_call_id, tool_name=tool_name, arguments=business_arguments,
                    request_key=request_key, request_id=request_id,
                    invoke=lambda: self._invoke(tool_name, business_arguments).model_dump(mode="json"),
                    failure=lambda exc: self._failure(tool_name, business_arguments, exc).model_dump(mode="json"),
                )
                return VoiceToolResponse.model_validate(response)
            except ToolRequestConflict as exc:
                return VoiceToolResponse(ok=False, error={"code": exc.code, "message": exc.message},
                                         guidance="Reuse the original arguments for a retry. Ask the caller before starting a separate request.")
            except ConcurrencyConflictError as exc:
                return self._failure(tool_name, business_arguments, exc)
        try:
            return self._invoke(tool_name, cleaned)
        except DomainError as exc:
            return self._failure(tool_name, cleaned, exc)

    def _failure(self, tool_name, cleaned, exc):
        logger.warning("voice_tool_failed", extra={"tool": tool_name, "error_code": exc.code})
        self._services.voice.record_tool_invocation(
            tool_name=tool_name, case_id=_case_id_of(cleaned, None), succeeded=False, error_code=exc.code
        )
        guidance = next((text for cls, text in _GUIDANCE if isinstance(exc, cls)), None)
        return VoiceToolResponse(ok=False, error={"code": exc.code, "message": exc.message, "details": exc.details}, guidance=guidance)

    def _invoke(self, tool_name, cleaned):
        result = self._toolbox.invoke(tool_name, self._actor, cleaned)
        self._services.voice.record_tool_invocation(
            tool_name=tool_name, case_id=_case_id_of(cleaned, result), succeeded=True, error_code=None
        )
        return VoiceToolResponse(ok=True, result=result)
