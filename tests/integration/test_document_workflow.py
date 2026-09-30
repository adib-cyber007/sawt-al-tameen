"""Real file transfer across the voice request -> documents -> review boundary."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from preauth.api.app import create_app
from preauth.application.assemblyai_post_call import AssemblyAIPostCallService
from preauth.domain.enums import HumanDecisionType
from tests.integration.helpers import REVIEWER, TREATMENT_DATE, decide
from tests.integration.test_assemblyai_post_call import (FakeAssemblyAIClient, VERIFY_ARGS, SESSION_ID,
    _ready_session, _settings, _post, _completed_event)

PDF = b"%PDF-1.4\nSynthetic test document\n%%EOF"
PORTAL = {"X-Gateway-Secret": "test-secret", "X-Actor-Type": "PROVIDER_PORTAL", "X-Actor-Id": "test-portal"}


def app_for(services, tmp_path):
    return create_app(services, _settings(gateway_secret="test-secret", document_store_dir=str(tmp_path / "documents")))


def upload(client, case_id, kind="CLINICAL_NOTES", content=PDF, media_type="application/pdf", headers=PORTAL):
    return client.post(f"/api/v1/cases/{case_id}/documents/upload", params={"document_type": kind, "title": "Synthetic test"},
                       content=content, headers={**headers, "Content-Type": media_type})


def test_full_voice_document_recheck_transcript_and_human_decision(services, tmp_path):
    app = app_for(services, tmp_path)
    gateway = app.state.assemblyai_voice_gateway
    verified = gateway.call("verify_caller", VERIFY_ARGS, SESSION_ID).result
    args = {"verification_id": verified["verification_id"], "procedure_code": "SP-20040",
            "treatment_date": TREATMENT_DATE, "estimated_cost_aed": 21000}
    first = gateway.call("check_coverage_rule", args, SESSION_ID).result
    assert first["outcome"] == "REQUEST_MORE_INFORMATION"
    case_id = first["case_id"]
    with TestClient(app) as client:
        for index, kind in enumerate(["CLINICAL_NOTES", "OPERATIVE_PLAN", "PRIOR_TREATMENT_RECORD"]):
            reply = upload(client, case_id, kind)
            assert reply.status_code == 201, reply.text
            doc = reply.json()
            assert doc["content_sha256"] == hashlib.sha256(PDF).hexdigest()
            fetched = client.get(f"/api/v1/cases/{case_id}/documents/{doc['id']}/content", headers=PORTAL)
            assert fetched.content == PDF
            assert fetched.headers["content-disposition"].startswith("attachment")
            needs = client.get(f"/api/v1/cases/{case_id}/required-information", headers=PORTAL).json()
            assert len(needs["missing_information"]) == 2 - index
        ready = gateway.call("check_coverage_rule", {**args, "case_reference": first["case_reference"]}, SESSION_ID).result
        assert ready["case_id"] == case_id
        assert ready["status"] == "PENDING_HUMAN_REVIEW" and ready["advisory_only"] is True
        logged = gateway.call("log_transcript", {"verification_id": verified["verification_id"],
            "case_reference": first["case_reference"], "summary": "Synthetic documents received, recommendation sent for review.",
            "outcome_communicated": "RECOMMENDATION_PREPARED"}, SESSION_ID)
        assert logged.ok
        services.review.assign_reviewer(case_id, REVIEWER)
        from preauth.domain.errors import OperationNotAllowedError
        with pytest.raises(OperationNotAllowedError) as blocked:
            decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE, assign=False)
        assert blocked.value.code == "CALL_RECORD_PENDING"
        fake = FakeAssemblyAIClient(); _ready_session(fake)
        app.state.assemblyai_post_call = AssemblyAIPostCallService(app.state.settings, services.voice, client=fake)
        assert _post(client, _completed_event()).status_code == 200
        assert not services.review.review_packet(case_id, REVIEWER).pending_call_conversation_ids
        assert decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE, assign=False).to_status.value == "APPROVED"
        assert upload(client, case_id).status_code == 409


def test_upload_auth_validation_and_private_storage(services, tmp_path, monkeypatch):
    app = app_for(services, tmp_path)
    gateway = app.state.assemblyai_voice_gateway
    verified = gateway.call("verify_caller", VERIFY_ARGS, SESSION_ID).result
    case = gateway.call("check_coverage_rule", {"verification_id": verified["verification_id"], "procedure_code": "SP-20040",
                 "treatment_date": TREATMENT_DATE, "estimated_cost_aed": 21000}, SESSION_ID).result
    with TestClient(app) as client:
        assert upload(client, case["case_id"], headers={}).status_code == 401
        assert upload(client, case["case_id"], content=b"<script>evil</script>").status_code == 422
        assert upload(client, case["case_id"], content=b"").status_code == 422
        reviewer = {**PORTAL, "X-Actor-Type": "HUMAN_REVIEWER"}
        assert upload(client, case["case_id"], headers=reviewer).status_code == 403
        monkeypatch.setattr("preauth.api.routes.documents.MAX_DOCUMENT_BYTES", 5)
        assert upload(client, case["case_id"]).status_code == 422
        assert not (tmp_path / "documents").exists()
        assert client.get("/documents").status_code == 200


def test_registration_failure_removes_uploaded_bytes(services, tmp_path, monkeypatch):
    from preauth.domain.errors import ConcurrencyConflictError
    from tests.integration.helpers import verify, check
    app = app_for(services, tmp_path)
    first = check(services, verify(services))
    def conflict(*args):
        raise ConcurrencyConflictError("Case changed during upload")
    monkeypatch.setattr(services.cases, "register_document", conflict)
    with TestClient(app) as client:
        assert upload(client, first.case_id).status_code == 409
    assert list((tmp_path / "documents").iterdir()) == []
