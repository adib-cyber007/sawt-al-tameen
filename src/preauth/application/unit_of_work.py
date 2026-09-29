import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from preauth.domain.actors import Actor
from preauth.domain.case_state import assert_transition
from preauth.domain.enums import AuditEventType, CaseStatus
from preauth.domain.errors import ConcurrencyConflictError
from preauth.infrastructure.clock import Clock, new_id
from preauth.infrastructure.db.models import AuditEvent, PreAuthorizationCase
from preauth.infrastructure.db.repositories import (
    AuditRepository,
    CaseRepository,
    CatalogueRepository,
    EvaluationRepository,
    ReviewRepository,
    VoiceChannelRepository,
)
from preauth.infrastructure.observability import request_id_var

logger = logging.getLogger("preauth.audit")

# Only explicitly scoped voice operations join a transaction. Ordinary API use cases
# retain their existing transaction boundaries. Context is local to the executing thread.
_shared_transaction: ContextVar[tuple[object, Session] | None] = ContextVar("shared_transaction", default=None)


@contextmanager
def shared_transaction(session_factory, session):
    token = _shared_transaction.set((session_factory, session))
    try:
        yield
    finally:
        _shared_transaction.reset(token)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unserialisable audit value of type {type(value).__name__}")


class AuditRecorder:
    """Appends immutable audit events. Sequences are contiguous per case within and across transactions."""

    def __init__(self, repo: AuditRepository, clock: Clock):
        self._repo = repo
        self._clock = clock
        self._next_sequence: dict[str, int] = {}

    def record(
        self, case_id: str, event_type: AuditEventType, actor: Actor, data: dict[str, Any] | None = None
    ) -> AuditEvent:
        sequence = self._next_sequence.get(case_id) or self._repo.max_sequence(case_id) + 1
        self._next_sequence[case_id] = sequence + 1
        event = AuditEvent(
            id=new_id(),
            case_id=case_id,
            sequence=sequence,
            event_type=event_type,
            actor_type=actor.type,
            actor_id=actor.id,
            occurred_at=self._clock.now(),
            request_id=request_id_var.get(),
            data=json.loads(json.dumps(data or {}, default=_json_default)),
        )
        self._repo.add(event)
        logger.info(
            "audit_event_recorded",
            extra={"event_type": event_type.value, "sequence": sequence, "actor_type": actor.type.value},
        )
        return event


class UnitOfWork:
    """One database transaction. Commits only when ``commit`` is called; otherwise rolls back."""

    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self.clock = clock

    def __enter__(self) -> "UnitOfWork":
        shared = _shared_transaction.get()
        self._joined = shared is not None and shared[0] is self._session_factory
        self.session = shared[1] if self._joined else self._session_factory()
        self.catalogue = CatalogueRepository(self.session)
        self.cases = CaseRepository(self.session)
        self.evaluations = EvaluationRepository(self.session)
        self.reviews = ReviewRepository(self.session)
        self.audit = AuditRecorder(AuditRepository(self.session), self.clock)
        self.audit_events = AuditRepository(self.session)
        self.voice = VoiceChannelRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._joined:
            return  # The outer operation owns rollback and closing, including savepoints.
        if exc_type is not None:
            self.session.rollback()
        self.session.close()

    def flush(self) -> None:
        try:
            self.session.flush()
        except StaleDataError as e:
            if not self._joined:
                self.session.rollback()
            raise ConcurrencyConflictError("The case was modified concurrently; reload and retry") from e

    def commit(self) -> None:
        self.flush()
        if not self._joined:
            self.session.commit()

    def transition(
        self,
        case: PreAuthorizationCase,
        target: CaseStatus,
        actor: Actor,
        *,
        reason: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """The only way case status changes. Validates against the state machine and records an audit event."""
        assert_transition(case.status, target, actor.type)
        previous = case.status
        case.status = target
        case.updated_at = self.clock.now()
        self.audit.record(
            case.id,
            AuditEventType.CASE_STATUS_CHANGED,
            actor,
            {"from_status": previous, "to_status": target, "reason": reason, **(data or {})},
        )
        logger.info(
            "case_status_changed",
            extra={"from_status": previous.value, "to_status": target.value, "reason": reason},
        )
