"""Commit a tool's business effects, audit linkage and saved response as one transaction.

The tools only perform local database work. Joining their existing units of work avoids
partially committed case/evaluation/review/callback steps. A crash rolls the entire attempt
back; a lost response after commit is recovered from this ledger. Unique database keys,
not process locks, arbitrate concurrent retries across workers.
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from preauth.application.unit_of_work import shared_transaction
from preauth.domain.errors import DomainError, ConcurrencyConflictError
from preauth.infrastructure.clock import new_id
from preauth.infrastructure.db.models import VoiceProviderCall, VoiceToolExecution


class ToolRequestConflict(DomainError):
    code = "TOOL_REQUEST_CONFLICT"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


class VoiceExecutionService:
    def __init__(self, session_factory, clock):
        self._session_factory = session_factory
        self._clock = clock

    def execute(self, *, actor_id, conversation_id, provider_call_id, tool_name, arguments,
                request_key, request_id, invoke, failure):
        arguments_hash = digest(arguments)
        request_hash = digest([tool_name, arguments, request_key, request_id])
        operation_key = digest(["explicit", request_key]) if request_key else arguments_hash
        # A unique-key loser starts a fresh transaction and reads the winner's committed
        # result. Never retry arbitrary business exceptions here.
        for attempt in range(3):
            claim_complete = False
            try:
                with self._session_factory.begin() as session:
                    with shared_transaction(self._session_factory, session):
                        receipt = session.scalar(select(VoiceProviderCall).where(
                            VoiceProviderCall.actor_id == actor_id,
                            VoiceProviderCall.conversation_id == conversation_id,
                            VoiceProviderCall.provider_call_id == provider_call_id,
                        )) if provider_call_id else None
                        if receipt is not None:
                            if receipt.request_hash != request_hash:
                                raise ToolRequestConflict("The provider call id was reused with different arguments")
                            return session.get(VoiceToolExecution, receipt.execution_id).response
                        if request_id:
                            execution = session.get(VoiceToolExecution, request_id)
                            if execution is None or (execution.actor_id, execution.conversation_id, execution.tool_name) != (
                                actor_id, conversation_id, tool_name
                            ):
                                raise ToolRequestConflict("The request id does not belong to this session and tool")
                        else:
                            execution = session.scalar(select(VoiceToolExecution).where(
                                VoiceToolExecution.actor_id == actor_id,
                                VoiceToolExecution.conversation_id == conversation_id,
                                VoiceToolExecution.tool_name == tool_name,
                                VoiceToolExecution.operation_key == operation_key,
                            ))
                        if execution is not None and execution.arguments_hash != arguments_hash:
                            raise ToolRequestConflict("A request key cannot be reused with different arguments")
                        if execution is None:
                            execution = VoiceToolExecution(
                                id=new_id(), actor_id=actor_id, conversation_id=conversation_id,
                                tool_name=tool_name, operation_key=operation_key,
                                arguments_hash=arguments_hash, state="running", response=None,
                                created_at=self._clock.now(),
                            )
                            session.add(execution)
                            session.flush()  # claim before any business side effect
                        if provider_call_id:
                            session.add(VoiceProviderCall(
                                id=new_id(), actor_id=actor_id, conversation_id=conversation_id,
                                provider_call_id=provider_call_id, execution_id=execution.id,
                                request_hash=request_hash,
                            ))
                            session.flush()
                        claim_complete = True
                        if execution.response is None:
                            try:
                                with session.begin_nested():
                                    response = invoke()
                            except ConcurrencyConflictError:
                                raise  # Roll back the claim too; a retry must be able to execute again.
                            except DomainError as exc:
                                response = failure(exc)
                            execution.response = {**response, "request_id": execution.id}
                            execution.state = "completed" if response["ok"] else "failed"
                            session.flush()
                        return execution.response
            except IntegrityError:
                # Only claim races are retryable, never errors inside business work.
                if claim_complete or attempt == 2:
                    raise
