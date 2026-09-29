"""Relational schema.

The catalogue tables (policy_tiers, procedures, coverage_terms, providers, members, escalation_rules,
onboarding_*) are loaded from ``knowledge_base/`` by ``preauth.seed``. That catalogue is the single source of
truth for every coverage decision: the rules engine reads these tables, and the same files are the agent's
knowledge base, so a cited section always exists in a retrievable document.

Append-only tables (enforced by database triggers in the migration): rule_evaluations, rule_results,
recommendations, review_decisions, audit_events, voice_tool_invocations, call_records, caller_verifications.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from preauth.domain.enums import (
    ActorType,
    AuditEventType,
    CallbackReason,
    CallbackStatus,
    CallerRole,
    CaseStatus,
    CloseReason,
    DecisionClass,
    DirectoryStatus,
    DocumentType,
    HumanDecisionType,
    PolicyStatus,
    RecommendationOutcome,
    ReviewerRole,
    RuleCategory,
    RuleOutcome,
    Urgency,
)
from preauth.infrastructure.db.types import JsonType, UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID = String(36)


def enum_column(enum_cls: type[StrEnum], constraint_name: str | None = None) -> Enum:
    """VARCHAR + CHECK constraint (portable). ``constraint_name`` is needed when a table has two columns of
    the same enum type, because the generated CHECK constraint is named after the enum."""
    return Enum(
        enum_cls,
        name=constraint_name or enum_cls.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=40,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --------------------------------------------------------------------------- catalogue


class PolicyTier(Base):
    """A policy tier (BASIC, ENHANCED, COMPREHENSIVE, EXECUTIVE) from knowledge_base/policy_tiers.json."""

    __tablename__ = "policy_tiers"

    tier_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    product_name: Mapped[str] = mapped_column(String(200))
    # 1 = Basic … 4 = Executive. Networks nest: a provider in a lower network is reachable by every higher tier.
    tier_rank: Mapped[int] = mapped_column(Integer)
    annual_limit_aed: Mapped[int] = mapped_column(Integer)
    pre_authorisation_threshold_aed: Mapped[int] = mapped_column(Integer)
    sub_limits_aed: Mapped[dict[str, Any]] = mapped_column(JsonType)
    co_payments_percent: Mapped[dict[str, Any]] = mapped_column(JsonType)
    co_payment_caps_aed: Mapped[dict[str, Any]] = mapped_column(JsonType)
    waiting_periods_months: Mapped[dict[str, Any]] = mapped_column(JsonType)
    network_id: Mapped[str] = mapped_column(String(60))
    network_name: Mapped[str] = mapped_column(String(80))
    out_of_network_covered: Mapped[bool] = mapped_column(Boolean)
    source_document: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)


class EscalationRule(Base):
    """ESC-### rules from knowledge_base/escalation_rules.json, cited verbatim when a case is escalated."""

    __tablename__ = "escalation_rules"

    rule_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    situation: Mapped[str] = mapped_column(Text)
    agent_action: Mapped[str] = mapped_column(Text)


class Procedure(Base):
    __tablename__ = "procedures"

    procedure_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    code_system: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(40))
    specialty_required: Mapped[str] = mapped_column(String(80))
    typical_billed_amount_aed: Mapped[int] = mapped_column(Integer)
    pre_authorisation_rule: Mapped[str] = mapped_column(String(40))
    minimum_tier: Mapped[str] = mapped_column(String(40))
    waiting_period_months: Mapped[int] = mapped_column(Integer)
    waiting_period_waived_for_emergency: Mapped[bool] = mapped_column(Boolean)
    decision_class: Mapped[DecisionClass] = mapped_column(enum_column(DecisionClass))
    escalation_rule_id: Mapped[str | None] = mapped_column(ForeignKey("escalation_rules.rule_id"))
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    exclusions: Mapped[list[str]] = mapped_column(JsonType)
    required_documents: Mapped[list[str]] = mapped_column(JsonType)

    escalation_rule: Mapped[EscalationRule | None] = relationship()


class CoverageTerm(Base):
    """How one tier covers one procedure. This is what the coverage rules read."""

    __tablename__ = "coverage_terms"
    __table_args__ = (UniqueConstraint("tier_id", "procedure_code"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    tier_id: Mapped[str] = mapped_column(ForeignKey("policy_tiers.tier_id"), index=True)
    procedure_code: Mapped[str] = mapped_column(ForeignKey("procedures.procedure_code"), index=True)
    covered: Mapped[bool] = mapped_column(Boolean)
    pre_authorisation_required: Mapped[bool] = mapped_column(Boolean)
    member_co_payment_percent: Mapped[int | None] = mapped_column(Integer)
    applicable_sub_limit_aed: Mapped[int | None] = mapped_column(Integer)
    reason_not_covered: Mapped[str | None] = mapped_column(Text)
    source_document: Mapped[str] = mapped_column(String(200))
    source_section: Mapped[str] = mapped_column(String(200))

    procedure: Mapped[Procedure] = relationship()
    tier: Mapped[PolicyTier] = relationship()


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    provider_number: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    emirate: Mapped[str] = mapped_column(String(60))
    area: Mapped[str] = mapped_column(String(80))
    facility_type: Mapped[str] = mapped_column(String(40))
    regulator: Mapped[str] = mapped_column(String(20))
    facility_licence_number: Mapped[str] = mapped_column(String(60))
    # Lowest network the facility belongs to; members on that tier and above can use it.
    minimum_network_rank: Mapped[int] = mapped_column(Integer)
    specialties: Mapped[list[str]] = mapped_column(JsonType)
    directory_status: Mapped[DirectoryStatus] = mapped_column(enum_column(DirectoryStatus))
    notes: Mapped[str | None] = mapped_column(Text)


class Member(Base):
    """A policyholder from knowledge_base/sample_members.json. Dependants are held as structured JSON."""

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    member_id: Mapped[str] = mapped_column(String(40), unique=True)
    policy_number: Mapped[str] = mapped_column(String(40), unique=True)
    given_name: Mapped[str] = mapped_column(String(100))
    family_name: Mapped[str] = mapped_column(String(100))
    nationality: Mapped[str] = mapped_column(String(60))
    date_of_birth: Mapped[date] = mapped_column(Date)
    emirates_id: Mapped[str] = mapped_column(String(30), unique=True)
    mobile: Mapped[str | None] = mapped_column(String(20))
    tier_id: Mapped[str] = mapped_column(ForeignKey("policy_tiers.tier_id"), index=True)
    policy_status: Mapped[PolicyStatus] = mapped_column(enum_column(PolicyStatus))
    policy_start_date: Mapped[date] = mapped_column(Date)
    policy_renewal_date: Mapped[date | None] = mapped_column(Date)
    policy_lapse_date: Mapped[date | None] = mapped_column(Date)
    emirate_of_residence: Mapped[str] = mapped_column(String(60))
    sponsor: Mapped[str | None] = mapped_column(String(200))
    dependents: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)

    tier: Mapped[PolicyTier] = relationship()


class OnboardingRequirement(Base):
    __tablename__ = "onboarding_requirements"

    requirement_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    issuing_authority: Mapped[str] = mapped_column(String(200))
    mandatory: Mapped[bool] = mapped_column(Boolean)
    validity_months: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class OnboardingApplication(Base):
    __tablename__ = "onboarding_applications"

    application_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    provider_name: Mapped[str] = mapped_column(String(200))
    provider_status: Mapped[str] = mapped_column(String(40))
    documents: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    outstanding: Mapped[list[str]] = mapped_column(JsonType)

    provider: Mapped[Provider] = relationship()


# --------------------------------------------------------------------------- caller verification


class CallerVerification(Base):
    """Result of verify_caller. check_coverage_rule requires one: the agent cannot check cover before verifying."""

    __tablename__ = "caller_verifications"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    caller_role: Mapped[CallerRole] = mapped_column(enum_column(CallerRole))
    organisation_name: Mapped[str] = mapped_column(String(200))
    caller_reference: Mapped[str] = mapped_column(String(60))
    caller_name: Mapped[str | None] = mapped_column(String(100))
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"))
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"))
    authorised: Mapped[bool] = mapped_column(Boolean)
    failure_code: Mapped[str | None] = mapped_column(String(60))
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime)

    provider: Mapped[Provider | None] = relationship()
    member: Mapped[Member | None] = relationship()


# --------------------------------------------------------------------------- cases


class PreAuthorizationCase(Base):
    __tablename__ = "pre_authorization_cases"
    __table_args__ = (CheckConstraint("estimated_cost_aed >= 0", name="estimated_cost_non_negative"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_reference: Mapped[str] = mapped_column(String(20), unique=True)
    status: Mapped[CaseStatus] = mapped_column(enum_column(CaseStatus), index=True)
    created_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    created_by_actor_id: Mapped[str] = mapped_column(String(100))

    verification_id: Mapped[str | None] = mapped_column(ForeignKey("caller_verifications.id"))
    caller_name: Mapped[str | None] = mapped_column(String(100))
    caller_role: Mapped[CallerRole | None] = mapped_column(enum_column(CallerRole))
    caller_organisation: Mapped[str | None] = mapped_column(String(200))

    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"), index=True)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), index=True)
    # Plain column, not a foreign key: a caller may quote a code that is not in the schedule, which is itself a
    # finding (ESC-005) and must still produce a case the medical director can review.
    procedure_code: Mapped[str | None] = mapped_column(String(40))
    treatment_date: Mapped[date | None] = mapped_column(Date)
    estimated_cost_aed: Mapped[int | None] = mapped_column(Integer)
    urgency: Mapped[Urgency | None] = mapped_column(enum_column(Urgency))
    diagnosis_code: Mapped[str | None] = mapped_column(String(10))
    clinical_summary: Mapped[str | None] = mapped_column(Text)

    review_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    assigned_reviewer_id: Mapped[str | None] = mapped_column(String(100))
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    close_reason: Mapped[CloseReason | None] = mapped_column(enum_column(CloseReason))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    version: Mapped[int] = mapped_column(Integer)

    __mapper_args__ = {"version_id_col": version}

    provider: Mapped[Provider | None] = relationship()
    member: Mapped[Member | None] = relationship()
    documents: Mapped[list["CaseDocument"]] = relationship(order_by="CaseDocument.registered_at")


class CaseDocument(Base):
    """Metadata for supporting documentation. Content lives in an external document store."""

    __tablename__ = "case_documents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    document_type: Mapped[DocumentType] = mapped_column(enum_column(DocumentType))
    title: Mapped[str] = mapped_column(String(200))
    storage_uri: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(100))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime)
    registered_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    registered_by_actor_id: Mapped[str] = mapped_column(String(100))


# --------------------------------------------------------------------------- evaluation & recommendation


class RuleDefinition(Base):
    __tablename__ = "rule_definitions"

    rule_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[RuleCategory] = mapped_column(enum_column(RuleCategory))
    first_registered_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"
    __table_args__ = (UniqueConstraint("case_id", "sequence"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    engine_name: Mapped[str] = mapped_column(String(80))
    engine_version: Mapped[str] = mapped_column(String(40))
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    triggered_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    triggered_by_actor_id: Mapped[str] = mapped_column(String(100))

    results: Mapped[list["RuleResultRecord"]] = relationship(order_by="RuleResultRecord.position")


class RuleResultRecord(Base):
    __tablename__ = "rule_results"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "rule_id"),
        ForeignKeyConstraint(
            ["rule_id", "rule_version"], ["rule_definitions.rule_id", "rule_definitions.version"]
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("rule_evaluations.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(80))
    rule_version: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[RuleOutcome] = mapped_column(enum_column(RuleOutcome))
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType)
    missing_information: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    escalation_rule_id: Mapped[str | None] = mapped_column(String(20))


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("rule_evaluations.id"), unique=True)
    outcome: Mapped[RecommendationOutcome] = mapped_column(enum_column(RecommendationOutcome))
    rationale: Mapped[str] = mapped_column(Text)
    determining_rule_ids: Mapped[list[str]] = mapped_column(JsonType)
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType)
    missing_information: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    escalation_citations: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    engine_name: Mapped[str] = mapped_column(String(80))
    engine_version: Mapped[str] = mapped_column(String(40))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    evaluation: Mapped[RuleEvaluation] = relationship()


# --------------------------------------------------------------------------- human review


class ReviewDecision(Base):
    """A human reviewer's decision. Never modifies the recommendation it responds to."""

    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "(decision = 'APPROVE' AND to_status = 'APPROVED')"
            " OR (decision = 'DENY' AND to_status = 'DENIED')"
            " OR (decision = 'REQUEST_INFORMATION' AND to_status = 'PENDING_INFORMATION')"
            " OR (decision = 'ESCALATE' AND to_status = 'ESCALATED')",
            name="decision_matches_status",
        ),
        CheckConstraint("length(rationale) > 0", name="rationale_present"),
        UniqueConstraint("case_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"))
    decision: Mapped[HumanDecisionType] = mapped_column(enum_column(HumanDecisionType))
    rationale: Mapped[str] = mapped_column(Text)
    reviewer_id: Mapped[str] = mapped_column(String(100))
    reviewer_role: Mapped[ReviewerRole] = mapped_column(enum_column(ReviewerRole))
    is_override: Mapped[bool] = mapped_column(Boolean)
    from_status: Mapped[CaseStatus] = mapped_column(enum_column(CaseStatus, "from_status_casestatus"))
    to_status: Mapped[CaseStatus] = mapped_column(enum_column(CaseStatus, "to_status_casestatus"))
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- audit


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[AuditEventType] = mapped_column(enum_column(AuditEventType))
    actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    actor_id: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    request_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JsonType)


# --------------------------------------------------------------------------- voice channel


class CallbackRequest(Base):
    """A request for a human to call back: ambiguous, non-rule-based, or out-of-scope enquiries."""

    __tablename__ = "callback_requests"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    caller_name: Mapped[str] = mapped_column(String(100))
    caller_organisation: Mapped[str | None] = mapped_column(String(200))
    caller_role: Mapped[CallerRole] = mapped_column(enum_column(CallerRole))
    callback_phone: Mapped[str] = mapped_column(String(20))
    preferred_language: Mapped[str] = mapped_column(String(5))
    reason: Mapped[CallbackReason] = mapped_column(enum_column(CallbackReason))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[CallbackStatus] = mapped_column(enum_column(CallbackStatus), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    created_by_actor_id: Mapped[str] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class VoiceToolInvocation(Base):
    """Links a voice conversation to the cases it touched. Used to require the call transcript before sign-off."""

    __tablename__ = "voice_tool_invocations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    invoked_at: Mapped[datetime] = mapped_column(UTCDateTime)


class TwilioMediaAdmission(Base):
    __tablename__ = "twilio_media_admissions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_sid: Mapped[str] = mapped_column(String(100), unique=True)
    stream_sid: Mapped[str] = mapped_column(String(100), unique=True)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    admitted_at: Mapped[datetime] = mapped_column(UTCDateTime)


class VoiceToolExecution(Base):
    """An atomic business operation and its replayable response, scoped to an actor/session."""

    __tablename__ = "voice_tool_executions"
    __table_args__ = (UniqueConstraint("actor_id", "conversation_id", "tool_name", "operation_key"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(100))
    conversation_id: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str] = mapped_column(String(64))
    operation_key: Mapped[str] = mapped_column(String(64))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    response: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class VoiceProviderCall(Base):
    __tablename__ = "voice_provider_calls"
    __table_args__ = (UniqueConstraint("actor_id", "conversation_id", "provider_call_id"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(100))
    conversation_id: Mapped[str] = mapped_column(String(100))
    provider_call_id: Mapped[str] = mapped_column(String(100))
    execution_id: Mapped[str] = mapped_column(ForeignKey("voice_tool_executions.id"))
    request_hash: Mapped[str] = mapped_column(String(64))


class CallRecord(Base):
    """Transcript and analysis delivered by the voice platform's post-call webhook."""

    __tablename__ = "call_records"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(100), unique=True)
    agent_id: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(40))
    call_duration_secs: Mapped[int | None] = mapped_column(Integer)
    transcript_summary: Mapped[str | None] = mapped_column(Text)
    call_successful: Mapped[str | None] = mapped_column(String(40))
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    analysis: Mapped[dict[str, Any]] = mapped_column(JsonType)
    call_metadata: Mapped[dict[str, Any]] = mapped_column(JsonType)
    event_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CallLog(Base):
    """In-call summary written by the log_transcript tool, before any sign-off language is spoken."""

    __tablename__ = "call_logs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    callback_id: Mapped[str | None] = mapped_column(ForeignKey("callback_requests.id"))
    caller_role: Mapped[CallerRole | None] = mapped_column(enum_column(CallerRole))
    outcome_communicated: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(UTCDateTime)


APPEND_ONLY_TABLES = (
    "rule_evaluations",
    "rule_results",
    "recommendations",
    "review_decisions",
    "audit_events",
    "voice_tool_invocations",
    "call_records",
    "caller_verifications",
    "call_logs",
)
