"""Voice-agent tool boundary.

The agent operates the system through exactly three tools:

    verify_caller        identify the calling organisation and, where relevant, the member
    check_coverage_rule  check a complete request against the benefit schedule
    log_transcript       record what the caller was told, and raise a callback where a human must follow up

**There is no tool that approves, denies, or finalises anything.** That is an architectural fact, not a prompt
instruction: recording a decision is a reviewer-only API that the voice actor has no credentials for. Tool inputs
are flat (strings, integers, enums) with a description on every field, because voice platforms accept only simple
parameter schemas and the model needs guidance for each value it fills in.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter, ValidationError

from preauth.application.commands import (
    CoverageCheckCommand,
    LogTranscriptCommand,
    StrictModel,
    VerifyCallerCommand,
)
from preauth.application.services import ApplicationServices
from preauth.application.views import CallLogView, CoverageCheckView, VerificationView
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, CallbackReason, CallerRole, CallOutcome, Urgency
from preauth.domain.errors import AuthorizationError, DomainError, NotFoundError

CASE_REFERENCE_DESCRIPTION = (
    "Case reference in the form PA- followed by 8 characters, as given to the caller earlier in this call."
)
VERIFICATION_DESCRIPTION = "The verification_id returned by verify_caller earlier in this call."


class ToolArgumentsInvalidError(DomainError):
    code = "TOOL_ARGUMENTS_INVALID"


# --------------------------------------------------------------------------- tool inputs


class VoiceRequestInput(StrictModel):
    request_id: str | None = Field(
        default=None, description="Application request_id returned by a previous attempt of this exact tool operation. Reuse it on retries."
    )
    request_key: str | None = Field(
        default=None, max_length=100, min_length=1,
        description="Omit normally: identical arguments in this session reuse the saved result. "
        "For a deliberately separate request or a fresh re-check after new evidence, choose a new stable key "
        "only when the caller asks for it. Reuse that same key on every retry; never generate a new key just because a response was interrupted.",
    )


class VerifyCallerInput(VoiceRequestInput):
    """Identify the caller, and the member if the call concerns one."""

    caller_role: CallerRole = Field(
        description="PROVIDER_STAFF for a clinic or hospital, BROKER for a broker acting for a provider, "
        "SUPPLIER for an onboarding enquiry, OTHER for anything else."
    )
    organisation_name: str = Field(description="Name of the clinic, hospital, brokerage or company calling.")
    caller_reference: str = Field(
        description="Provider number in the form PRV- followed by 5 digits for a clinic, hospital or broker acting "
        "for one; for a supplier, the onboarding application reference such as ONB-APP-2026-0007."
    )
    caller_name: str | None = Field(default=None, description="Name of the person on the call.")
    member_policy_number: str | None = Field(
        default=None,
        description="Member's policy number, format POL-SA-YYYY-NNNNNN. Send together with "
        "member_date_of_birth. Omit for supplier or onboarding calls.",
    )
    member_date_of_birth: date | None = Field(
        default=None,
        description="Member's date of birth as YYYY-MM-DD, used to verify the policy. Send together with "
        "member_policy_number.",
    )


class CheckCoverageRuleInput(VoiceRequestInput):
    """A complete pre-authorisation request. Do not call this with partial details."""

    verification_id: str = Field(description=VERIFICATION_DESCRIPTION)
    procedure_code: str = Field(description="Procedure code in the form SP- followed by 5 digits, e.g. SP-20050.")
    treatment_date: date = Field(description="Planned date of treatment as YYYY-MM-DD.")
    estimated_cost_aed: int = Field(
        description="Estimated cost of the treatment in AED, as a whole number without separators."
    )
    urgency: Urgency | None = Field(
        default=None, description="STANDARD, or EXPEDITED if the caller says the request is clinically urgent."
    )
    diagnosis_code: str | None = Field(default=None, description="Primary diagnosis as an ICD-10 code, if given.")
    clinical_summary: str | None = Field(
        default=None, description="Short clinical justification in the caller's words, if given."
    )
    case_reference: str | None = Field(
        default=None,
        description="Only when re-checking a case opened earlier, for example after documents were submitted. "
        + CASE_REFERENCE_DESCRIPTION,
    )


class LogTranscriptInput(VoiceRequestInput):
    """Record the outcome of the call. Call this before telling the caller what happens next."""

    summary: str = Field(description="Two or three sentences: what was requested and what the caller was told.")
    outcome_communicated: CallOutcome = Field(
        description="RECOMMENDATION_PREPARED, MORE_INFORMATION_REQUESTED, ESCALATED, CALLER_NOT_VERIFIED, "
        "ONBOARDING_ENQUIRY or OUT_OF_SCOPE."
    )
    verification_id: str | None = Field(default=None, description=VERIFICATION_DESCRIPTION)
    case_reference: str | None = Field(default=None, description=CASE_REFERENCE_DESCRIPTION)
    caller_name: str | None = Field(default=None, description="Name of the person on the call.")
    callback_phone: str | None = Field(
        default=None,
        description="Number to call back, international format with no spaces, e.g. +971501234567. Required when "
        "a human needs to follow up.",
    )
    preferred_language: str | None = Field(
        default=None,
        description="ISO 639-1 language code requested for a human follow-up, if known. Defaults to en.",
    )
    callback_reason: CallbackReason | None = Field(
        default=None, description="Why a human must follow up, when that is not obvious from the outcome."
    )


# --------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_type: Any
    handler: Callable[[ApplicationServices, Actor, Any], BaseModel]


def _convert(build: Callable[[], Any]) -> Any:
    """Run a flat-to-command conversion, reporting failures as invalid tool arguments."""
    try:
        return build()
    except ValidationError as e:
        raise ToolArgumentsInvalidError(
            "Arguments are invalid",
            details={"errors": [{"location": list(err["loc"]), "message": err["msg"]} for err in e.errors()]},
        ) from e


def _verify_caller(s: ApplicationServices, a: Actor, i: VerifyCallerInput) -> VerificationView:
    command = _convert(lambda: VerifyCallerCommand(**i.model_dump(exclude_none=True, exclude={"request_id", "request_key"})))
    return s.desk.verify_caller(a, command)


def _check_coverage_rule(s: ApplicationServices, a: Actor, i: CheckCoverageRuleInput) -> CoverageCheckView:
    command = _convert(lambda: CoverageCheckCommand(**i.model_dump(exclude_none=True, exclude={"request_id", "request_key"})))
    return s.desk.check_coverage_rule(a, command)


def _log_transcript(s: ApplicationServices, a: Actor, i: LogTranscriptInput) -> CallLogView:
    command = _convert(lambda: LogTranscriptCommand(**i.model_dump(exclude_none=True, exclude={"request_id", "request_key"})))
    return s.desk.log_transcript(a, command)


TOOLS: tuple[Tool, ...] = (
    Tool(
        "verify_caller",
        "Verify the calling organisation, and the member if the call concerns one, before discussing anything "
        "policy-specific or patient-specific. Returns a verification_id needed by check_coverage_rule, the "
        "member's tier and dependants when verified, and a failure reason when not.",
        VerifyCallerInput,
        VerificationView,
        _verify_caller,
    ),
    Tool(
        "check_coverage_rule",
        "Check a complete pre-authorisation request against the benefit schedule. Returns a prepared "
        "recommendation for human sign-off, a list of missing documents, or an escalation citing the rule that "
        "applies. Never state a coverage answer without calling this first, and never call it with partial details.",
        CheckCoverageRuleInput,
        CoverageCheckView,
        _check_coverage_rule,
    ),
    Tool(
        "log_transcript",
        "Record what the caller was told and return the reference to read back. Raises a human callback when the "
        "outcome needs follow-up. Call this before speaking any sign-off or next-step language.",
        LogTranscriptInput,
        CallLogView,
        _log_transcript,
    ),
)

# Nothing in this list may ever decide a case. Guarded by tests, not by convention.
FORBIDDEN_TOOL_CONCEPTS = ("approve", "deny", "decision", "decide", "authorise", "authorize", "finalise", "sign_off")


class AgentToolbox:
    def __init__(self, services: ApplicationServices, tools: tuple[Tool, ...] = TOOLS):
        self._services = services
        self._tools = {t.name: t for t in tools}

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        """Tool definitions in a model-agnostic form (name, description, JSON Schemas)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_model.model_json_schema(),
                "output_schema": TypeAdapter(t.output_type).json_schema(),
            }
            for t in self._tools.values()
        ]

    def invoke(self, name: str, actor: Actor, arguments: dict[str, Any]) -> dict[str, Any]:
        if actor.type is not ActorType.VOICE_AGENT:
            raise AuthorizationError(
                "Agent tools may only be invoked by a VOICE_AGENT actor",
                code="VOICE_AGENT_REQUIRED",
                details={"actor_type": actor.type},
            )
        tool = self._tools.get(name)
        if tool is None:
            raise NotFoundError(
                f"Unknown tool {name!r}", code="TOOL_NOT_FOUND", details={"available_tools": self.tool_names}
            )
        try:
            parsed = tool.input_model.model_validate(arguments)
        except ValidationError as e:
            raise ToolArgumentsInvalidError(
                f"Arguments for {name} are invalid",
                details={
                    "tool": name,
                    "errors": [{"location": list(err["loc"]), "message": err["msg"]} for err in e.errors()],
                },
            ) from e
        return tool.handler(self._services, actor, parsed).model_dump(mode="json")
