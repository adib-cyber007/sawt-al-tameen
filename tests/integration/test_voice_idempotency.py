"""Fault-injected retries must preserve exactly one committed business operation."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import threading

import pytest
from sqlalchemy import func, select

from preauth.agent_tools.toolbox import AgentToolbox
from preauth.agent_tools.voice_gateway import VoiceToolGateway
from preauth.application.services import build_services
from preauth.api.assemblyai_bridge import AssemblyAIToolCoordinator
from preauth.api import assemblyai_bridge
from preauth.infrastructure.db.models import (
    PreAuthorizationCase, CallerVerification, CallLog, CallbackRequest,
    VoiceToolExecution, VoiceProviderCall, VoiceToolInvocation, Recommendation, AuditEvent,
)
from tests.integration.helpers import TREATMENT_DATE

VERIFY = {"caller_role": "PROVIDER_STAFF", "organisation_name": "Al Hudaiba Crescent Hospital",
          "caller_reference": "PRV-30011", "member_policy_number": "POL-SA-2026-100001",
          "member_date_of_birth": "1986-04-17"}


def gateway_for(services):
    return VoiceToolGateway(services, AgentToolbox(services))


def counts(session_factory, *models):
    with session_factory() as session:
        return [session.scalar(select(func.count()).select_from(model)) for model in models]


def coverage_args(gateway):
    verification = gateway.call("verify_caller", VERIFY, "sess_test", provider_call_id="verify-1")
    assert verification.ok
    return {"verification_id": verification.result["verification_id"], "procedure_code": "SP-20040",
            "treatment_date": TREATMENT_DATE, "estimated_cost_aed": 21000}


def test_coverage_replay_after_lost_response_and_process_restart(services, seeded_session_factory, clock):
    gateway = gateway_for(services)
    arguments = coverage_args(gateway)
    first = gateway.call("check_coverage_rule", arguments, "sess_test", provider_call_id="coverage-1")
    assert first.ok
    restarted = gateway_for(build_services(seeded_session_factory, clock))
    assert restarted.call("check_coverage_rule", arguments, "sess_test", provider_call_id="coverage-1") == first
    # Same business request, new provider call id, and normalised spelling/defaults.
    retry = {**arguments, "procedure_code": " sp-20040 ", "urgency": "STANDARD"}
    assert restarted.call("check_coverage_rule", retry, "sess_test", provider_call_id="coverage-2") == first
    assert restarted.call("check_coverage_rule", {**arguments, "request_id": first.request_id}, "sess_test", provider_call_id="coverage-3") == first
    assert counts(seeded_session_factory, PreAuthorizationCase, Recommendation, VoiceToolInvocation) == [1, 1, 2]


def test_request_identity_conflicts_and_explicit_new_request(services, seeded_session_factory):
    gateway = gateway_for(services)
    args = coverage_args(gateway)
    first = gateway.call("check_coverage_rule", {**args, "request_key": "intake-1"}, "sess_test", provider_call_id="c1")
    assert first.ok
    for arguments, call_id in [({**args, "estimated_cost_aed": 20000, "request_key": "intake-1"}, "c2"),
                               ({**args, "request_key": "intake-2"}, "c1")]:
        result = gateway.call("check_coverage_rule", arguments, "sess_test", provider_call_id=call_id)
        assert result.error["code"] == "TOOL_REQUEST_CONFLICT"
    separate = {**args, "request_key": "intake-2"}
    second = gateway.call("check_coverage_rule", separate, "sess_test", provider_call_id="c3")
    assert second.ok and second.result["case_id"] != first.result["case_id"]
    assert gateway.call("check_coverage_rule", separate, "sess_test", provider_call_id="c4") == second
    assert counts(seeded_session_factory, PreAuthorizationCase) == [2]


def test_request_ids_cannot_replay_another_sessions_result(services):
    gateway = gateway_for(services)
    first = gateway.call("verify_caller", VERIFY, "session_a", provider_call_id="same-id")
    result = gateway.call("verify_caller", {**VERIFY, "request_id": first.request_id}, "session_b", provider_call_id="same-id")
    assert result.error["code"] == "TOOL_REQUEST_CONFLICT"
    other = gateway.call("verify_caller", VERIFY, "session_b", provider_call_id="same-id")
    assert other.ok and other.result["verification_id"] != first.result["verification_id"]


def test_concurrent_duplicate_calls_execute_once(services, seeded_session_factory):
    gateway = gateway_for(services)
    args = coverage_args(gateway)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: gateway.call("check_coverage_rule", args, "sess_test", provider_call_id=f"call-{index}"), range(4)))
    assert all(result.ok and result == results[0] for result in results)
    assert counts(seeded_session_factory, PreAuthorizationCase, Recommendation, VoiceProviderCall) == [1, 1, 5]


@pytest.mark.parametrize("stage", ["evaluation", "review", "audit"])
def test_failure_after_case_work_rolls_back_every_step_then_retry_succeeds(services, seeded_session_factory, monkeypatch, stage):
    gateway = gateway_for(services)
    args = coverage_args(gateway)
    if stage == "review":
        args["procedure_code"] = "SP-99999"
    target, method = {"evaluation": (services.evaluation, "submit_for_evaluation"),
                      "review": (services.review, "request_human_review"),
                      "audit": (services.voice, "record_tool_invocation")}[stage]
    original = getattr(target, method)

    def fail_after_work(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated failure after business work")

    monkeypatch.setattr(target, method, fail_after_work)
    with pytest.raises(OSError):
        gateway.call("check_coverage_rule", args, "sess_test", provider_call_id="c1")
    assert counts(seeded_session_factory, PreAuthorizationCase, Recommendation, AuditEvent, VoiceToolExecution) == [0, 0, 0, 1]
    monkeypatch.setattr(target, method, original)
    assert gateway.call("check_coverage_rule", args, "sess_test", provider_call_id="c1").ok
    assert counts(seeded_session_factory, PreAuthorizationCase, Recommendation) == [1, 1]


def test_callback_and_log_are_atomic_and_replayable(services, seeded_session_factory, monkeypatch):
    gateway = gateway_for(services)
    args = {"summary": "The caller requested help outside pre-authorisation.", "outcome_communicated": "OUT_OF_SCOPE",
            "callback_phone": "+971501234567", "caller_name": "Test Caller"}
    original = services.callbacks.request_callback

    def fail_after_callback(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("crash after callback creation")

    monkeypatch.setattr(services.callbacks, "request_callback", fail_after_callback)
    with pytest.raises(OSError):
        gateway.call("log_transcript", args, "sess_test", provider_call_id="log-1")
    assert counts(seeded_session_factory, CallbackRequest, CallLog, VoiceToolExecution) == [0, 0, 0]
    monkeypatch.setattr(services.callbacks, "request_callback", original)
    first = gateway.call("log_transcript", args, "sess_test", provider_call_id="log-1")
    assert first.ok
    assert gateway.call("log_transcript", args, "sess_test", provider_call_id="log-2") == first
    assert counts(seeded_session_factory, CallbackRequest, CallLog, VoiceToolExecution) == [1, 1, 1]


@pytest.mark.parametrize("fault", ["interrupt", "timeout"])
def test_committed_case_is_recovered_after_interrupted_or_timed_out_delivery(services, seeded_session_factory, monkeypatch, fault):
    gateway = gateway_for(services)
    args = coverage_args(gateway)
    committed, release = threading.Event(), threading.Event()
    if fault == "timeout":
        monkeypatch.setattr(assemblyai_bridge, "TOOL_TIMEOUT_SECONDS", .02)

    class LostResponseGateway:
        def call(self, *args, **kwargs):
            response = gateway.call(*args, **kwargs)
            committed.set()
            assert release.wait(3)
            return response

    async def scenario():
        coordinator = AssemblyAIToolCoordinator(LostResponseGateway())
        try:
            await coordinator.handle({"type": "session.ready", "session_id": "sess_test"})
            await coordinator.handle({"type": "tool.call", "call_id": "c1", "name": "check_coverage_rule", "arguments": args})
            async with asyncio.timeout(2):
                while not committed.is_set():
                    await asyncio.sleep(.001)
            await coordinator.handle({"type": "reply.done", "reply_id": "fc-c1", "status": "completed"})
            if fault == "interrupt":
                await coordinator.handle({"type": "input.speech.started"})
            else:
                async with asyncio.timeout(2):
                    while not coordinator._pending[0].expired:
                        await asyncio.sleep(.001)
            # New provider id after losing the original response still returns the committed case.
            recovered = await asyncio.to_thread(gateway.call, "check_coverage_rule", args, "sess_test", "c2")
            assert recovered.ok
            assert counts(seeded_session_factory, PreAuthorizationCase, Recommendation) == [1, 1]
        finally:
            release.set()
            await coordinator.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("transient", [False, True])
def test_domain_failure_rolls_back_business_steps_and_transient_failure_can_retry(services, seeded_session_factory, monkeypatch, transient):
    from preauth.domain.errors import OperationNotAllowedError, ConcurrencyConflictError
    gateway = gateway_for(services)
    args = coverage_args(gateway)
    original = services.evaluation.submit_for_evaluation

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise (ConcurrencyConflictError if transient else OperationNotAllowedError)("Injected after evaluation")

    monkeypatch.setattr(services.evaluation, "submit_for_evaluation", fail)
    first = gateway.call("check_coverage_rule", args, "sess_test", provider_call_id="c1")
    assert not first.ok
    assert counts(seeded_session_factory, PreAuthorizationCase, Recommendation, AuditEvent) == [0, 0, 0]
    monkeypatch.setattr(services.evaluation, "submit_for_evaluation", original)
    replay = gateway.call("check_coverage_rule", args, "sess_test", provider_call_id="c1")
    if transient:
        assert replay.ok
    else:
        assert replay == first
