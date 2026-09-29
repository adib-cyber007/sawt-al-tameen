"""AssemblyAI post-call delivery, artifact retries, and sign-off linkage."""

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from preauth.api.app import create_app
from preauth.application.assemblyai_post_call import AssemblyAIPostCallService
from preauth.domain.enums import AuditEventType, DocumentType
from preauth.infrastructure.assemblyai_client import AssemblyAIError
from preauth.infrastructure.assemblyai_signature import sign
from preauth.infrastructure.db.models import CallRecord
from preauth.infrastructure.settings import Settings, VoiceProvider
from tests.integration.helpers import REVIEWER, TREATMENT_DATE, add_documents

SECRET = "assemblyai-webhook-secret"
SESSION_ID = "sess_aai_001"
VERIFY_ARGS = {
    "caller_role": "PROVIDER_STAFF",
    "organisation_name": "Al Hudaiba Crescent Hospital",
    "caller_reference": "prv-30011",
    "caller_name": "Aisha Rahman",
    "member_policy_number": "POL-SA-2026-100001",
    "member_date_of_birth": "1986-04-17",
}


class FakeAssemblyAIClient:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.timelines: dict[str, dict] = {}
        self.get_calls: list[str] = []
        self.fail = False

    def get_session(self, session_id: str) -> dict:
        self.get_calls.append(session_id)
        if self.fail:
            raise AssemblyAIError("upstream unavailable", status=503)
        return self.sessions[session_id]

    def download_json(self, url: str) -> dict:
        if self.fail:
            raise AssemblyAIError("artifact unavailable", status=503)
        return self.timelines[url]


def _settings(**overrides) -> Settings:
    values = {
        "voice_provider": VoiceProvider.ASSEMBLYAI,
        "assemblyai_api_key": "test-key",
        "assemblyai_webhook_secret": SECRET,
    }
    values.update(overrides)
    return Settings(**values)


def _client(services, fake: FakeAssemblyAIClient, settings: Settings | None = None):
    app = create_app(services, settings or _settings())
    app.state.assemblyai_post_call = AssemblyAIPostCallService(app.state.settings, services.voice, client=fake)
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, payload: dict, *, secret: str = SECRET, timestamp: int | None = None):
    raw = json.dumps(payload).encode()
    signed_at = int(time.time()) if timestamp is None else timestamp
    return client.post(
        "/api/v1/voice/assemblyai/post-call",
        content=raw,
        headers={"X-AAI-Signature": sign(raw, secret, signed_at), "content-type": "application/json"},
    )


def _completed_event(session_id: str = SESSION_ID) -> dict:
    return {
        "event_id": "evt_001",
        "event": "session.completed",
        "timestamp": "2026-09-19T12:00:00Z",
        "session": {
            "session_id": session_id,
            "agent_id": "agent_aai",
            "status": "completed",
            "duration_seconds": 212,
            "public_close_reason": "client_end",
        },
    }


def _ready_session(fake: FakeAssemblyAIClient, session_id: str = SESSION_ID) -> None:
    url = f"https://artifacts.example/{session_id}/timeline.json?signature=secret"
    fake.sessions[session_id] = {
        "id": session_id,
        "agent_id": "agent_aai",
        "status": "completed",
        "duration_seconds": 212,
        "created_at": "2026-09-19T11:56:28Z",
        "ended_at": "2026-09-19T12:00:00Z",
        "public_close_reason": "client_end",
        "artifacts": [{"type": "timeline", "url": url}],
    }
    fake.timelines[url] = {
        "turns": [
            {
                "status": "complete",
                "trigger": "user_speech",
                "user_transcript": "I need approval for a knee arthroscopy.",
                "user_confidence": 0.96,
                "time_to_first_audio_ms": 240,
                "agent_text": "The recommendation is ready for human review.",
                "tool_calls": [
                    {
                        "name": "log_transcript",
                        "arguments": {
                            "summary": "Clinic requested arthroscopy pre-authorisation.",
                            "outcome_communicated": "RECOMMENDATION_PREPARED",
                        },
                    }
                ],
            }
        ]
    }


def _case_ready_for_review(app, services) -> str:
    gateway = app.state.assemblyai_voice_gateway
    verification = gateway.call("verify_caller", VERIFY_ARGS, SESSION_ID).result
    first = gateway.call(
        "check_coverage_rule",
        {
            "verification_id": verification["verification_id"],
            "procedure_code": "SP-20040",
            "treatment_date": TREATMENT_DATE,
            "estimated_cost_aed": 21_000,
        },
        SESSION_ID,
    ).result
    add_documents(
        services,
        first["case_id"],
        [DocumentType.CLINICAL_NOTES, DocumentType.OPERATIVE_PLAN, DocumentType.PRIOR_TREATMENT_RECORD],
    )
    ready = gateway.call(
        "check_coverage_rule",
        {
            "verification_id": verification["verification_id"],
            "procedure_code": "SP-20040",
            "treatment_date": TREATMENT_DATE,
            "estimated_cost_aed": 21_000,
            "case_reference": first["case_reference"],
        },
        SESSION_ID,
    ).result
    return ready["case_id"]


def test_completed_session_records_timeline_and_unblocks_the_linked_case(services, session_factory):
    fake = FakeAssemblyAIClient()
    _ready_session(fake)
    app = create_app(services, _settings())
    app.state.assemblyai_post_call = AssemblyAIPostCallService(app.state.settings, services.voice, client=fake)
    case_id = _case_ready_for_review(app, services)
    services.review.assign_reviewer(case_id, REVIEWER)
    assert services.review.review_packet(case_id, REVIEWER).pending_call_conversation_ids == [SESSION_ID]

    with TestClient(app, raise_server_exceptions=False) as client:
        response = _post(client, _completed_event())

    assert response.status_code == 200
    assert response.json()["linked_case_ids"] == [case_id]
    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.pending_call_conversation_ids == []
    assert len(packet.calls) == 1
    call = packet.calls[0]
    assert call.platform == "assemblyai"
    assert call.conversation_id == SESSION_ID
    assert call.call_duration_secs == 212
    assert call.transcript_summary == "Clinic requested arthroscopy pre-authorisation."
    assert [turn["role"] for turn in call.transcript] == ["caller", "tool", "agent"]
    assert call.analysis["timeline_metrics"]["average_user_confidence"] == 0.96
    assert AuditEventType.CALL_RECORDED in [event.event_type for event in packet.audit_history]
    with session_factory() as session:
        stored = session.scalar(select(CallRecord).where(CallRecord.conversation_id == SESSION_ID))
        assert stored.call_metadata["started_at"] == "2026-09-19T11:56:28Z"
        assert stored.call_metadata["close_reason"] == "client_end"


def test_missing_artifact_is_retryable_then_idempotent(services):
    fake = FakeAssemblyAIClient()
    fake.sessions[SESSION_ID] = {"id": SESSION_ID, "status": "completed", "artifacts": []}
    with _client(services, fake) as client:
        pending = _post(client, _completed_event())
        assert pending.status_code == 503
        assert pending.json()["error"]["code"] == "WEBHOOK_ARTIFACT_PENDING"

        _ready_session(fake)
        first = _post(client, _completed_event())
        second = _post(client, _completed_event())

    assert first.status_code == second.status_code == 200
    assert first.json()["call_record_id"] == second.json()["call_record_id"]
    assert second.json()["detail"] == "Already recorded"


def test_webhook_rejects_invalid_delivery_and_maps_provider_outages(services):
    fake = FakeAssemblyAIClient()
    _ready_session(fake)
    with _client(services, fake) as client:
        assert _post(client, _completed_event(), secret="wrong").status_code == 401
        assert client.post("/api/v1/voice/assemblyai/post-call", content=b"{}").status_code == 401
        malformed = _post(client, {"type": "session.completed"})
        assert malformed.status_code == 400
        assert malformed.json()["error"]["code"] == "WEBHOOK_PAYLOAD_INVALID"
        fake.fail = True
        outage = _post(client, _completed_event())
        assert outage.status_code == 503
        assert outage.json()["error"]["code"] == "VOICE_PROVIDER_UNAVAILABLE"


def test_webhook_ignores_other_events_without_fetching_a_session(services):
    fake = FakeAssemblyAIClient()
    with _client(services, fake) as client:
        response = _post(
            client,
            {"event": "session.started", "session": {"session_id": SESSION_ID}},
        )
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert fake.get_calls == []


def test_webhook_is_disabled_without_provider_configuration(services):
    fake = FakeAssemblyAIClient()
    settings = Settings(voice_provider=VoiceProvider.ASSEMBLYAI)
    with _client(services, fake, settings) as client:
        response = client.post("/api/v1/voice/assemblyai/post-call", content=b"{}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CHANNEL_NOT_CONFIGURED"

def test_typed_correction_is_durable_scoped_and_included_in_final_call_record(services, session_factory):
    from preauth.infrastructure.db.models import VoiceTextCorrection
    correction_id = services.voice.record_text_correction(SESSION_ID, 'The correct name is Morgan.')
    services.voice.record_text_correction('unrelated-session', 'This must not appear.')
    fake = FakeAssemblyAIClient()
    _ready_session(fake)
    client = _client(services, fake)
    assert _post(client, _completed_event()).status_code == 200
    assert _post(client, _completed_event()).status_code == 200
    with session_factory() as session:
        record = session.scalar(select(CallRecord).where(CallRecord.conversation_id == SESSION_ID))
        corrections = [turn for turn in record.transcript if turn.get('source') == 'typed_correction']
        assert len(corrections) == 1
        assert corrections[0]['message'] == 'The correct name is Morgan.'
        assert corrections[0]['correction_id'] == correction_id
        assert corrections[0]['delivery'] == 'submitted_to_bridge'
        assert session.get(VoiceTextCorrection, correction_id).content == 'The correct name is Morgan.'
