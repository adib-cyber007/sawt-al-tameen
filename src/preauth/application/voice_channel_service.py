"""Voice-channel bookkeeping: which conversations touched which cases, and the post-call record of each call.

The post-call record is what makes a voice interaction auditable. A human decision on a case is blocked until
every conversation that touched the case has delivered its transcript (see ``pending_conversation_ids``).
"""

import logging
import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import IntegrityError

from preauth.application.unit_of_work import UnitOfWork
from preauth.domain.actors import SYSTEM_ACTOR
from preauth.domain.enums import AuditEventType
from preauth.domain.errors import NotFoundError
from preauth.infrastructure.clock import Clock, new_id
from preauth.infrastructure.db.models import CallRecord, VoiceToolInvocation, TwilioMediaAdmission
from preauth.infrastructure import assemblyai_media_token
from preauth.domain.actors import Actor
from preauth.infrastructure.observability import actor_var, bind_case_id, conversation_id_var, request_id_var

logger = logging.getLogger("preauth.voice")


class PostCallOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    detail: str
    call_record_id: str | None = None
    linked_case_ids: list[str] = []


def bind_voice_context(actor: Actor, conversation_id: str | None) -> None:
    """Attach the voice actor and conversation to logs, audit linkage and callback records for this request."""
    actor_var.set(f"{actor.type.value}:{actor.id}")
    conversation_id_var.set(conversation_id)


def pending_conversation_ids(uow: UnitOfWork, case_id: str) -> list[str]:
    conversations = uow.voice.conversation_ids_for_case(case_id)
    recorded = {r.conversation_id for r in uow.voice.call_records(conversations)}
    return [c for c in conversations if c not in recorded]


class VoiceChannelService:
    ASSEMBLYAI_PLATFORM = "assemblyai"
    LOCAL_PLATFORM = "local"

    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    def record_tool_invocation(
        self, *, tool_name: str, case_id: str | None, succeeded: bool, error_code: str | None
    ) -> None:
        conversation_id = conversation_id_var.get()
        if conversation_id is None:
            return  # Not a voice conversation (e.g. direct API test); nothing to link.
        with UnitOfWork(self._session_factory, self._clock) as uow:
            if case_id is not None:
                try:
                    uow.cases.get(case_id)
                except NotFoundError:
                    case_id = None  # the tool referenced a case that does not exist; keep the invocation, drop link
            uow.voice.add_invocation(
                VoiceToolInvocation(
                    id=new_id(),
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    case_id=case_id,
                    succeeded=succeeded,
                    error_code=error_code,
                    request_id=request_id_var.get(),
                    invoked_at=self._clock.now(),
                )
            )
            uow.commit()

    def claim_twilio_stream(self, token: str, secret: str, stream_sid: str) -> bool:
        verified = assemblyai_media_token.verify(token, secret)
        try:
            with self._session_factory.begin() as session:
                session.add(TwilioMediaAdmission(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    call_sid=verified.call_sid, stream_sid=stream_sid,
                    expires_at=verified.expires_at, admitted_at=self._clock.now(),
                ))
            return True
        except IntegrityError:
            return False

    @staticmethod
    def _link_cases(uow: UnitOfWork, record: CallRecord) -> list[str]:
        """Add a CALL_RECORDED audit event to every case the conversation touched, and return their ids."""
        case_ids = uow.voice.case_ids_for_conversation(record.conversation_id)
        for case_id in case_ids:
            bind_case_id(case_id)
            uow.audit.record(
                case_id,
                AuditEventType.CALL_RECORDED,
                SYSTEM_ACTOR,
                {
                    "call_record_id": record.id,
                    "conversation_id": record.conversation_id,
                    "agent_id": record.agent_id,
                    "platform": record.platform,
                    "call_duration_secs": record.call_duration_secs,
                    "call_successful": record.call_successful,
                    "transcript_summary": record.transcript_summary,
                    "transcript_turns": len(record.transcript),
                },
            )
        return case_ids

    def record_local_call(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        transcript: list[dict[str, Any]],
        summary: str | None = None,
        call_duration_secs: int | None = None,
        analysis: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PostCallOutcome:
        """Finalise a locally handled call.

        The local channel has no post-call webhook to wait for: the process that handled the call already holds
        the transcript, so it stores it directly when the call ends. The resulting row is the same immutable
        ``call_records`` row a hosted call produces, which is what makes the "transcript before sign-off" rule
        apply identically to both channels. ``platform`` distinguishes them for anyone reading the audit trail.
        """
        return self._record_call(
            conversation_id=conversation_id,
            agent_id=agent_id,
            platform=self.LOCAL_PLATFORM,
            status="done",
            transcript=transcript,
            summary=summary,
            call_successful="unknown",
            call_duration_secs=call_duration_secs,
            analysis=analysis,
            metadata=metadata,
            event_timestamp=int(self._clock.now().timestamp()),
            log_event="conversation_finished",
        )

    def record_assemblyai_call(
        self,
        *,
        session_id: str,
        agent_id: str,
        status: str | None,
        transcript: list[dict[str, Any]],
        summary: str | None,
        call_successful: str | None,
        call_duration_secs: int | None,
        analysis: dict[str, Any],
        metadata: dict[str, Any],
        event_timestamp: int | None,
    ) -> PostCallOutcome:
        """Persist a normalized AssemblyAI session using the same append-only audit boundary as every channel."""
        return self._record_call(
            conversation_id=session_id,
            agent_id=agent_id,
            platform=self.ASSEMBLYAI_PLATFORM,
            status=status,
            transcript=transcript,
            summary=summary,
            call_successful=call_successful,
            call_duration_secs=call_duration_secs,
            analysis=analysis,
            metadata=metadata,
            event_timestamp=event_timestamp,
            log_event="call_recorded",
        )

    def _record_call(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        platform: str,
        status: str | None,
        transcript: list[dict[str, Any]],
        summary: str | None,
        call_successful: str | None,
        call_duration_secs: int | None,
        analysis: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        event_timestamp: int | None,
        log_event: str,
    ) -> PostCallOutcome:
        conversation_id_var.set(conversation_id)
        with UnitOfWork(self._session_factory, self._clock) as uow:
            existing = uow.voice.call_record(conversation_id)
            if existing is not None:
                return PostCallOutcome(
                    accepted=True,
                    detail="Already recorded",
                    call_record_id=existing.id,
                    linked_case_ids=uow.voice.case_ids_for_conversation(conversation_id),
                )
            record = CallRecord(
                id=new_id(),
                conversation_id=conversation_id,
                agent_id=agent_id,
                platform=platform,
                status=status,
                call_duration_secs=call_duration_secs,
                transcript_summary=summary,
                call_successful=call_successful,
                transcript=transcript,
                analysis=analysis or {},
                call_metadata=metadata or {},
                event_timestamp=event_timestamp,
                received_at=self._clock.now(),
            )
            uow.voice.add_call_record(record)
            uow.flush()
            case_ids = self._link_cases(uow, record)
            uow.commit()
            logger.info(
                log_event,
                extra={"linked_cases": len(case_ids), "turns": len(transcript)},
            )
            return PostCallOutcome(
                accepted=True, detail="Recorded", call_record_id=record.id, linked_case_ids=case_ids
            )
