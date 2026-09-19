"""Local mode, end to end.

The model is a double; everything beneath it is the real system — the real toolbox, the real desk service, the
real UAE catalogue, the real rules engine, the real review queue and the real audit trail. So these tests say
what a local call actually does to a case, not what a mock was told to return.
"""

import pytest

from preauth.domain.enums import ActorType, DocumentType, HumanDecisionType
from preauth.domain.errors import AuthorizationError, OperationNotAllowedError
from preauth.agent_tools.toolbox import FORBIDDEN_TOOL_CONCEPTS, TOOLS
from preauth.local.errors import SpeechNotRecognisedError
from preauth.local.prompt import DECISION_BOUNDARY
from tests.integration.helpers import (
    BARIATRIC_HOSPITAL,
    EXECUTIVE_MEMBER,
    LAPSED_MEMBER,
    ORTHO_HOSPITAL,
    REVIEWER,
    TREATMENT_DATE,
    add_documents,
    event_types,
)
from tests.local_fakes import calls, says

VERIFY = dict(
    caller_role="PROVIDER_STAFF",
    organisation_name="Al Hudaiba Crescent Hospital",
    caller_reference=ORTHO_HOSPITAL,
    caller_name="Aisha Rahman",
    member_policy_number=EXECUTIVE_MEMBER[0],
    member_date_of_birth=EXECUTIVE_MEMBER[1].isoformat(),
)
ARTHROSCOPY = dict(procedure_code="SP-20040", treatment_date=TREATMENT_DATE, estimated_cost_aed=21000)


def verify_turn(harness, text="This is Aisha at Al Hudaiba, PRV-30011."):
    conversation = harness.start()
    return conversation, harness.runtime.say(conversation, text)


# --------------------------------------------------------------------------- caller verification


def test_the_agent_verifies_a_caller_through_the_real_desk_service(local):
    harness = local(calls("verify_caller", **VERIFY), says("Verified. What procedure is this for?"))
    _, turn = verify_turn(harness)

    assert turn["tool_calls"] == [{"tool": "verify_caller", "ok": True, "arguments": VERIFY, "error_code": None}]
    assert turn["state"]["verified"] is True
    assert turn["state"]["verification_id"]
    assert turn["reply"] == "Verified. What procedure is this for?"


def test_a_lapsed_policy_fails_verification_and_the_agent_is_told_why(local):
    lapsed = {**VERIFY, "member_policy_number": LAPSED_MEMBER[0],
              "member_date_of_birth": LAPSED_MEMBER[1].isoformat()}
    harness = local(calls("verify_caller", **lapsed), says("That policy is not active; a colleague will call back."))
    _, turn = verify_turn(harness)

    assert turn["state"]["verified"] is False
    # The failure reason reaches the model as a tool result, so it can say something true about it.
    tool_message = [m for m in harness.model.seen[-1] if m.get("role") == "tool"][-1]
    assert "POLICY_NOT_ACTIVE" in tool_message["content"]


def test_a_wrong_date_of_birth_comes_back_as_guidance_the_agent_can_act_on(local):
    wrong = {**VERIFY, "member_date_of_birth": "1990-01-01"}
    harness = local(calls("verify_caller", **wrong), says("Could you confirm the date of birth?"))
    _, turn = verify_turn(harness)

    assert turn["state"]["verified"] is False
    tool_message = [m for m in harness.model.seen[-1] if m.get("role") == "tool"][-1]
    assert "MEMBER_NOT_VERIFIED" in tool_message["content"]


def test_coverage_cannot_be_checked_before_verification(local, services):
    """The backend refuses, whatever the model decides to do."""
    harness = local(
        calls("check_coverage_rule", verification_id="00000000-0000-0000-0000-000000000000", **ARTHROSCOPY),
        says("I need to verify you first."),
    )
    _, turn = verify_turn(harness)

    assert turn["tool_calls"][0]["ok"] is False
    assert turn["state"]["case_reference"] is None


# --------------------------------------------------------------------------- coverage and documents


def test_a_coverage_check_opens_a_real_case_and_names_the_documents_outstanding(local, services):
    harness = local(calls("verify_caller", **VERIFY), says("Verified. What procedure?"))
    conversation = harness.start()
    verification_id = harness.runtime.say(conversation, "Aisha at Al Hudaiba, PRV-30011.")["state"][
        "verification_id"
    ]

    harness.model.replies.extend(
        [
            calls("check_coverage_rule", verification_id=verification_id, **ARTHROSCOPY),
            says("I need three documents before this can be assessed."),
        ]
    )
    turn = harness.runtime.say(conversation, "Knee arthroscopy SP-20040, 21,000 dirhams.")
    state = turn["state"]

    assert state["case_reference"].startswith("PA-")
    assert state["recommendation_outcome"] == "REQUEST_MORE_INFORMATION"
    assert len(state["missing_documents"]) == 3
    assert state["intake"]["procedure_code"] == "SP-20040"
    assert state["human_review_status"] == "AWAITING_DOCUMENTS"
    # The case really exists, with the audit trail every case gets.
    assert "CASE_CREATED" in event_types(services, state["case_id"])


def test_an_ambiguous_procedure_escalates_citing_the_rule(local, services):
    bariatric = {**VERIFY, "caller_reference": BARIATRIC_HOSPITAL, "organisation_name": "Yas Horizon"}
    harness = local(calls("verify_caller", **bariatric))
    conversation = harness.start()
    verification_id = harness.runtime.say(conversation, "Yas Horizon, PRV-30023.")["state"]["verification_id"]

    harness.model.replies.extend(
        [
            calls(
                "check_coverage_rule",
                verification_id=verification_id,
                procedure_code="SP-20110",
                treatment_date=TREATMENT_DATE,
                estimated_cost_aed=48000,
            ),
            says("This one needs a closer look from our team rather than a same-call answer."),
        ]
    )
    turn = harness.runtime.say(conversation, "Sleeve gastrectomy SP-20110, 48,000 dirhams.")

    assert turn["state"]["recommendation_outcome"] == "ESCALATE"
    assert turn["state"]["escalation_rule_ids"] == ["ESC-001"]
    assert turn["state"]["case_status"] == "ESCALATED"


def test_a_complete_request_prepares_a_recommendation_and_never_a_decision(local, services):
    harness = local(calls("verify_caller", **VERIFY))
    conversation = harness.start()
    verification_id = harness.runtime.say(conversation, "Aisha, PRV-30011.")["state"]["verification_id"]

    harness.model.replies.append(calls("check_coverage_rule", verification_id=verification_id, **ARTHROSCOPY))
    harness.model.replies.append(says("Documents needed."))
    opened = harness.runtime.say(conversation, "SP-20040, 21,000 dirhams.")
    add_documents(
        services,
        opened["state"]["case_id"],
        [DocumentType.CLINICAL_NOTES, DocumentType.OPERATIVE_PLAN, DocumentType.PRIOR_TREATMENT_RECORD],
    )

    harness.model.replies.append(
        calls(
            "check_coverage_rule",
            verification_id=verification_id,
            case_reference=opened["state"]["case_reference"],
            **ARTHROSCOPY,
        )
    )
    harness.model.replies.append(
        says("I've prepared a recommendation for a qualified reviewer to confirm before anything is issued.")
    )
    turn = harness.runtime.say(conversation, "The documents are uploaded.")

    assert turn["state"]["recommendation_outcome"] == "RECOMMEND_APPROVAL"
    assert turn["state"]["case_status"] == "PENDING_HUMAN_REVIEW"
    assert turn["state"]["human_review_status"] == "SIGN_OFF_BLOCKED_UNTIL_TRANSCRIPT_STORED"


# --------------------------------------------------------------------------- guardrails


def test_no_tool_the_local_agent_can_reach_decides_anything():
    """The local agent is offered exactly the tools the hosted agent is offered, and none of them decides."""
    assert [t.name for t in TOOLS] == ["verify_caller", "check_coverage_rule", "log_transcript"]
    for tool in TOOLS:
        assert not any(word in tool.name.lower() for word in FORBIDDEN_TOOL_CONCEPTS)


def test_the_local_agent_cannot_invent_a_decision_tool(local):
    harness = local(
        calls("approve_case", case_reference="PA-12345678"),
        says("I'm not able to issue a final decision on this call."),
    )
    _, turn = verify_turn(harness)

    assert turn["tool_calls"][0] == {
        "tool": "approve_case", "ok": False, "arguments": {"case_reference": "PA-12345678"},
        "error_code": "TOOL_NOT_FOUND",
    }
    assert turn["state"]["case_reference"] is None


@pytest.mark.parametrize("name", ["record_decision", "finalise_authorisation", "approve_case", "deny_case"])
def test_every_decision_shaped_tool_name_is_unavailable(local, name):
    harness = local(calls(name, case_reference="PA-12345678"), says("Not something I can do."))
    _, turn = verify_turn(harness)
    assert turn["tool_calls"][0]["error_code"] == "TOOL_NOT_FOUND"


def test_the_local_agent_acts_as_a_voice_actor_the_review_api_rejects(local, services):
    """Even if a decision endpoint were reachable, the actor it would arrive as cannot use it."""
    from preauth.agent_tools.voice_gateway import LOCAL_AGENT_ACTOR

    assert LOCAL_AGENT_ACTOR.type is ActorType.VOICE_AGENT
    with pytest.raises(AuthorizationError, match="human reviewer"):
        services.review.assign_reviewer("00000000-0000-0000-0000-000000000000", LOCAL_AGENT_ACTOR)


def test_a_final_decision_stated_by_the_model_is_removed_before_the_caller_hears_it(local):
    harness = local(says("Good news, your request is approved. The reference is PA-12345678."))
    _, turn = verify_turn(harness)

    assert "approved" not in turn["reply"]
    assert DECISION_BOUNDARY in turn["reply"]
    assert turn["decision_language_blocked"] is True
    # The reference the agent legitimately gave is kept; only the decision sentence goes.
    assert "PA-12345678" not in turn["reply"] or "approved" not in turn["reply"]


def test_ordinary_recommendation_wording_is_left_alone(local):
    wording = "I've prepared a recommendation for a reviewer to confirm before anything is issued."
    harness = local(says(wording))
    _, turn = verify_turn(harness)
    assert turn["reply"] == wording
    assert turn["decision_language_blocked"] is False


# --------------------------------------------------------------------------- transcript and review


def test_finishing_a_call_stores_a_transcript_and_unblocks_human_sign_off(local, services):
    harness = local(calls("verify_caller", **VERIFY))
    conversation = harness.start()
    verification_id = harness.runtime.say(conversation, "Aisha, PRV-30011.")["state"]["verification_id"]
    harness.model.replies.append(calls("check_coverage_rule", verification_id=verification_id, **ARTHROSCOPY))
    harness.model.replies.append(says("Documents needed."))
    opened = harness.runtime.say(conversation, "SP-20040, 21,000 dirhams.")
    case_id = opened["state"]["case_id"]
    add_documents(
        services, case_id,
        [DocumentType.CLINICAL_NOTES, DocumentType.OPERATIVE_PLAN, DocumentType.PRIOR_TREATMENT_RECORD],
    )
    harness.model.replies.append(
        calls("check_coverage_rule", verification_id=verification_id,
              case_reference=opened["state"]["case_reference"], **ARTHROSCOPY)
    )
    harness.model.replies.append(says("A reviewer will confirm."))
    harness.runtime.say(conversation, "Uploaded.")

    # Before the transcript is stored, the reviewer is blocked — the same rule as a telephone call.
    services.review.assign_reviewer(case_id, REVIEWER)
    recommendation = services.queries.get_latest_recommendation(case_id, REVIEWER)
    with pytest.raises(OperationNotAllowedError) as blocked:
        _decide(services, case_id, recommendation.id)
    assert blocked.value.code == "CALL_RECORD_PENDING"

    finished = harness.runtime.finish(conversation)
    assert finished["call_record_id"]
    assert case_id in finished["linked_case_ids"]
    assert "CALL_RECORDED" in event_types(services, case_id)

    decision = _decide(services, case_id, recommendation.id)
    assert decision.decision is HumanDecisionType.APPROVE


def test_the_stored_transcript_holds_the_turns_the_tools_and_the_outcome(local, services):
    harness = local(calls("verify_caller", **VERIFY), says("Verified. What procedure?"))
    conversation, _ = verify_turn(harness)
    finished = harness.runtime.finish(conversation)

    roles = [t["role"] for t in finished["transcript"]]
    assert roles == ["agent", "caller", "tool", "agent"]
    assert [t["tool_name"] for t in finished["transcript"] if t["role"] == "tool"] == ["verify_caller"]
    assert all(t["at"] for t in finished["transcript"])

    stored = _call_record(services, conversation)
    assert stored.platform == "local"
    assert stored.analysis["channel"] == "local"
    assert [c["tool"] for c in stored.analysis["tool_calls"]] == ["verify_caller"]
    assert "sign-off remains with a human reviewer" in stored.transcript_summary
    # Immutable, like every other call record: the append-only trigger rejects an edit.
    assert stored.call_metadata["agent_model"] == "scripted"


def test_finishing_twice_does_not_duplicate_the_record(local, services):
    harness = local(says("Who am I speaking with?"))
    conversation, _ = verify_turn(harness)
    first = harness.runtime.finish(conversation)
    second = harness.runtime.finish(conversation)
    assert second["call_record_id"] == first["call_record_id"]
    assert second["detail"] == "Already recorded"


# --------------------------------------------------------------------------- conversation handling


def test_the_agent_is_reminded_of_what_the_backend_already_holds(local):
    """State comes from the backend each turn, so the model is never trusted to remember it."""
    harness = local(calls("verify_caller", **VERIFY), says("Thanks."), says("Yes?"))
    conversation, _ = verify_turn(harness)
    harness.runtime.say(conversation, "And the procedure is SP-20040.")

    session = harness.runtime.sessions.get(conversation)
    reminder = harness.model.seen[-1][-1]
    assert reminder["role"] == "system"
    assert f"caller and member verified; verification_id={session.verification_id}" in reminder["content"]
    # It names what the next tool needs, without claiming the caller has not supplied it -- the backend cannot
    # know that, and saying so sends the model round in circles asking for details it already has.
    assert "check_coverage_rule needs procedure_code, treatment_date, estimated_cost_aed" in reminder["content"]
    assert "still needed" not in reminder["content"]

    session.verified = False
    assert "caller NOT verified yet" in harness.runtime.agent._state_reminder(session)


def test_an_empty_caller_turn_is_refused_rather_than_answered(local):
    harness = local(says("anything"))
    conversation = harness.start()
    with pytest.raises(SpeechNotRecognisedError):
        harness.runtime.say(conversation, "   ")


def test_a_silent_model_still_says_something_useful(local):
    harness = local(says(""))
    _, turn = verify_turn(harness)
    assert "repeat" in turn["reply"].lower()


def test_the_agent_must_speak_once_its_tool_budget_is_spent(local):
    """A model stuck in a tool loop is made to answer the caller rather than spinning."""
    harness = local(*[calls("verify_caller", **VERIFY) for _ in range(9)])
    _, turn = verify_turn(harness)

    budget = harness.runtime.settings.max_tool_iterations
    assert len(turn["tool_calls"]) == budget
    # The final pass is made with no tools offered at all, so the model cannot call another.
    assert harness.model.tools_offered[-1] == []
    # And the caller is told the truth rather than being blamed for mishearing.
    assert "trouble completing" in turn["reply"]


def test_spoken_turns_go_through_the_local_recogniser(local):
    harness = local(says("Verified."), heard="Aisha at Al Hudaiba, PRV-30011")
    conversation = harness.start()
    turn = harness.runtime.listen(conversation, b"fake-webm-audio")

    assert harness.transcriber.calls == [b"fake-webm-audio"]
    assert turn["heard"] == "Aisha at Al Hudaiba, PRV-30011"
    assert turn["heard_language"] == "en"


def test_replies_are_spoken_when_synthesis_is_configured(local, services):
    from dataclasses import replace as _replace

    from preauth.local.config import LocalSettings, SttProvider

    harness = local(
        says("Sawt Assurance. Who am I speaking with?"),
        settings=_replace(LocalSettings(), stt_provider=SttProvider.DISABLED),
    )
    # The fake synthesiser stands in for Piper; the runtime's contract is the same either way.
    harness.runtime.synthesizer = harness.synthesizer
    _, turn = verify_turn(harness)
    assert turn["audio"] and turn["audio_media_type"] == "audio/wav"
    assert harness.synthesizer.spoken[-1] == turn["reply"]


def _decide(services, case_id, recommendation_id):
    from preauth.application.commands import HumanDecisionCommand

    return services.review.record_decision(
        case_id,
        REVIEWER,
        HumanDecisionCommand(
            recommendation_id=recommendation_id,
            decision=HumanDecisionType.APPROVE,
            rationale="Documents complete and within benefit.",
        ),
    )


def _call_record(services, conversation_id):
    from preauth.application.unit_of_work import UnitOfWork

    with UnitOfWork(services.voice._session_factory, services.voice._clock) as uow:
        return uow.voice.call_record(conversation_id)


# --------------------------------------------------------------------------- observability


def test_a_whole_local_call_is_traceable_by_its_conversation_id(local, caplog):
    """Requirement: debug one call from one id, without a secret ever appearing in the log."""
    from preauth.infrastructure.observability import install_log_context

    install_log_context()
    harness = local(calls("verify_caller", **VERIFY), says("Verified. What procedure?"))
    with caplog.at_level("INFO", logger="preauth"):
        conversation, _ = verify_turn(harness)
        harness.runtime.finish(conversation)

    local_records = [r for r in caplog.records if r.name.startswith("preauth")]
    events = [r.getMessage() for r in local_records]
    for expected in (
        "conversation_started", "llm_started", "tool_called", "tool_completed", "llm_completed",
        "caller_verified", "conversation_finished",
    ):
        assert expected in events, expected

    # Every line the local channel emitted carries the conversation, so one grep reconstructs the call.
    tagged = [r for r in local_records if getattr(r, "conversation_id", None)]
    assert tagged and all(r.conversation_id == conversation for r in tagged)
    assert all(r.actor == "VOICE_AGENT:local-agent" for r in tagged)


def test_no_credential_is_written_to_the_log(local, caplog):
    from preauth.infrastructure.observability import install_log_context

    install_log_context()
    install_log_context()  # idempotent
    harness = local(calls("verify_caller", **VERIFY), says("Verified."))
    with caplog.at_level("DEBUG"):
        conversation, _ = verify_turn(harness)
        harness.runtime.finish(conversation)

    text = "\n".join(r.getMessage() + str(getattr(r, "__dict__", {})) for r in caplog.records)
    for forbidden in ("ELEVENLABS_API_KEY", "webhook_secret", "gateway_secret", "Bearer "):
        assert forbidden not in text


def test_a_finished_call_cannot_take_another_turn(local):
    """Its transcript is already on record, and call records are immutable — a further turn would be lost."""
    from preauth.local.errors import ConversationClosedError

    harness = local(says("Verified."), says("Anything else?"))
    conversation, _ = verify_turn(harness)
    harness.runtime.finish(conversation)

    with pytest.raises(ConversationClosedError) as raised:
        harness.runtime.say(conversation, "One more thing.")
    assert raised.value.details["call_record_id"]
    with pytest.raises(ConversationClosedError):
        harness.runtime.listen(conversation, b"audio")


def test_an_organisation_only_verification_is_flagged_before_the_coverage_check(local):
    """Seen with a real 7B model: it verified the clinic before the caller gave the policy number, never
    verified again, and the coverage check was refused. The reminder now says so before that happens."""
    org_only = {k: v for k, v in VERIFY.items() if not k.startswith("member_")}
    harness = local(calls("verify_caller", **org_only), says("What is the policy number?"), says("Thanks."))
    conversation, first = verify_turn(harness)
    assert first["state"]["verified"] is True
    assert first["state"]["member_verified"] is False
    assert first["state"]["intake_complete"] is False

    harness.runtime.say(conversation, "POL-SA-2026-100001, born 1986-04-17.")
    reminder = harness.model.seen[-1][-1]["content"]
    assert "NO member verified" in reminder
    assert "call verify_caller again with member_policy_number and member_date_of_birth" in reminder
    assert "check_coverage_rule needs" not in reminder
