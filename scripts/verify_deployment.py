"""End-to-end check of a running deployment, exercising exactly what the voice agent will do.

Run this against your public URL before pointing the hosted voice provider at it. It walks a synthetic
pre-authorisation call from verification to the sign-off boundary, and verifies the guardrails along the way.

    export PREAUTH_VOICE_TOOL_TOKEN=...             # required diagnostic token
    export PREAUTH_GATEWAY_SECRET=...               # if the deployment sets one
    export PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET=...    # to test the AssemblyAI webhook boundary
    uv run python scripts/verify_deployment.py --base-url https://your-backend.example

The deployment must have the catalogue loaded (`python -m preauth.seed`). The case this creates is closed at the
end, and everything it touches is synthetic.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from typing import Any

from preauth.infrastructure.assemblyai_signature import sign as sign_assemblyai

CONVERSATION_ID = f"verify_{uuid.uuid4().hex[:12]}"
PROVIDER = "PRV-30011"                          # Al Hudaiba Crescent Hospital, active, Orthopaedics
MEMBER = ("POL-SA-2026-100001", "1986-04-17")   # Fatima Al Mansoori, Executive tier
LAPSED_MEMBER = ("POL-SA-2026-100008", "1990-12-04")
PROCEDURE = "SP-20040"                          # Knee arthroscopy: covered, needs three documents
AMBIGUOUS_PROCEDURE = "SP-20110"                # Sleeve gastrectomy: ambiguous, escalates under ESC-001
REVIEWER = {"X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "verify-reviewer", "X-Actor-Roles": "CLINICAL_REVIEWER"}

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (passed if ok else failed).append(name)
    if ok and detail in ("None", "{}"):
        detail = ""
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    return ok


class Client:
    def __init__(self, base_url: str, voice_token: str, gateway_secret: str | None):
        self.base = base_url.rstrip("/")
        self.voice_headers = {"Authorization": f"Bearer {voice_token}", "X-Conversation-ID": CONVERSATION_ID}
        self.gateway = {"X-Gateway-Secret": gateway_secret} if gateway_secret else {}

    def request(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, Any]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
                return resp.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, payload.decode(errors="replace")
        except urllib.error.URLError as e:
            return 0, str(e)

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        status, body = self.request("POST", f"/api/v1/voice/tools/{name}", arguments, self.voice_headers)
        if status != 200:
            return {"ok": False, "error": {"code": f"HTTP_{status}", "message": str(body)}}
        return body

    def staff(self, method: str, path: str, body: Any = None, actor: dict[str, str] | None = None):
        headers = {**self.gateway, **(actor or {"X-Actor-Type": "PROVIDER_PORTAL", "X-Actor-Id": "verify-script"})}
        return self.request(method, path, body, headers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("PREAUTH_PUBLIC_BASE_URL", "http://localhost:8000"))
    args = parser.parse_args()

    token = os.environ.get("PREAUTH_VOICE_TOOL_TOKEN", "").strip()
    if not token:
        print("PREAUTH_VOICE_TOOL_TOKEN is not set", file=sys.stderr)
        return 2
    gateway_secret = os.environ.get("PREAUTH_GATEWAY_SECRET", "").strip() or None
    provider = "assemblyai"
    assemblyai_webhook_secret = os.environ.get("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET", "").strip() or None
    client = Client(args.base_url, token, gateway_secret)
    treatment_date = (date.today() + timedelta(days=21)).isoformat()
    print(f"Verifying {client.base}  (voice provider {provider}; conversation {CONVERSATION_ID})\n")

    print("Reachability and authentication")
    status, body = client.request("GET", "/health")
    if not check("health endpoint responds", status == 200 and body == {"status": "ok"}, str(body)[:80]):
        print("\nBackend not reachable; nothing else can be checked.")
        return 1
    status, _ = client.request("POST", "/api/v1/voice/tools/verify_caller", {}, {"Authorization": "Bearer wrong"})
    check("voice tools reject a wrong token", status == 401, f"got HTTP {status}")
    if gateway_secret:
        status, _ = client.request("GET", "/api/v1/review/queues/CLINICAL_REVIEW", headers=REVIEWER)
        check("staff API rejects a missing gateway secret", status == 401, f"got HTTP {status}")
    status, _ = client.staff("GET", "/api/v1/review/queues/CLINICAL_REVIEW", actor=REVIEWER)
    check("reviewer API reachable with credentials", status == 200, f"got HTTP {status}")

    print("\nCaller verification")
    verification = client.tool(
        "verify_caller",
        {
            "caller_role": "PROVIDER_STAFF", "organisation_name": "Al Hudaiba Crescent Hospital",
            "caller_reference": PROVIDER, "caller_name": "Verification Script",
            "member_policy_number": MEMBER[0], "member_date_of_birth": MEMBER[1],
        },
    )
    if not verification.get("ok"):
        check("caller verified", False, str((verification.get("error") or {}).get("code")))
        print("\nThe deployment has no catalogue loaded. Run: python -m preauth.seed")
        return 1
    result = verification["result"]
    check(
        "active member verified with tier and dependants",
        result["authorised"] and result["member"]["tier"]["tier_id"] == "EXECUTIVE"
        and len(result["member"]["dependents"]) == 2,
        f"tier {result['member']['tier']['tier_id']}",
    )
    verification_id = result["verification_id"]

    lapsed = client.tool(
        "verify_caller",
        {
            "caller_role": "PROVIDER_STAFF", "organisation_name": "Al Hudaiba Crescent Hospital",
            "caller_reference": PROVIDER, "member_policy_number": LAPSED_MEMBER[0],
            "member_date_of_birth": LAPSED_MEMBER[1],
        },
    )
    check(
        "lapsed member is rejected",
        lapsed["result"]["authorised"] is False and lapsed["result"]["failure_code"] == "POLICY_NOT_ACTIVE",
        str(lapsed["result"]["failure_code"]),
    )
    wrong_dob = client.tool(
        "verify_caller",
        {
            "caller_role": "PROVIDER_STAFF", "organisation_name": "Al Hudaiba Crescent Hospital",
            "caller_reference": PROVIDER, "member_policy_number": MEMBER[0],
            "member_date_of_birth": "1990-01-01",
        },
    )
    check(
        "wrong date of birth is rejected",
        wrong_dob["result"]["failure_code"] == "MEMBER_NOT_VERIFIED",
        str(wrong_dob["result"]["failure_code"]),
    )
    supplier = client.tool(
        "verify_caller",
        {"caller_role": "SUPPLIER", "organisation_name": "Gulf Medical Supplies",
         "caller_reference": "ONB-APP-2026-0007"},
    )
    check(
        "supplier onboarding status returned",
        bool(supplier["result"]["onboarding"]["outstanding_requirements"]),
        f"{len(supplier['result']['onboarding']['outstanding_requirements'])} outstanding",
    )

    print("\nCoverage checks")
    unverified = client.tool(
        "check_coverage_rule",
        {"verification_id": supplier["result"]["verification_id"], "procedure_code": PROCEDURE,
         "treatment_date": treatment_date, "estimated_cost_aed": 21000},
    )
    check(
        "a supplier cannot request a coverage check",
        unverified["ok"] is False, str((unverified.get("error") or {}).get("code")),
    )

    first = client.tool(
        "check_coverage_rule",
        {"verification_id": verification_id, "procedure_code": PROCEDURE,
         "treatment_date": treatment_date, "estimated_cost_aed": 21000},
    )
    first_result = first.get("result") or {}
    check(
        "missing documents are itemised",
        first_result.get("outcome") == "REQUEST_MORE_INFORMATION"
        and len(first_result.get("missing_information", [])) == 3,
        str(first_result.get("outcome")),
    )
    case_reference, case_id = first_result["case_reference"], first_result["case_id"]

    status = 0
    for document_type in ("CLINICAL_NOTES", "OPERATIVE_PLAN", "PRIOR_TREATMENT_RECORD"):
        status, _ = client.staff(
            "POST", f"/api/v1/cases/{case_id}/documents",
            {"document_type": document_type, "title": f"Verification {document_type}",
             "storage_uri": f"docstore://verify/{document_type.lower()}.pdf", "media_type": "application/pdf"},
        )
        if status != 201:
            break
    check("documents registered through the provider API", status == 201, f"got HTTP {status}")

    second = client.tool(
        "check_coverage_rule",
        {"verification_id": verification_id, "procedure_code": PROCEDURE, "treatment_date": treatment_date,
         "estimated_cost_aed": 21000, "case_reference": case_reference},
    )
    coverage = second.get("result") or {}
    check("complete request prepares a recommendation",
          coverage.get("outcome") == "RECOMMEND_APPROVAL", str(coverage.get("outcome")))
    check("recommendation is advisory only", coverage.get("advisory_only") is True)
    check(
        "sources cite the benefit schedule in knowledge_base/",
        any("Schedule of Benefits" in s["document"] for s in coverage.get("sources", [])),
        str([s["document"] for s in coverage.get("sources", [])][:1])[:60],
    )
    check("case routed to a human queue",
          coverage.get("status") == "PENDING_HUMAN_REVIEW", str(coverage.get("status")))

    ambiguous = client.tool(
        "check_coverage_rule",
        {"verification_id": verification_id, "procedure_code": AMBIGUOUS_PROCEDURE,
         "treatment_date": treatment_date, "estimated_cost_aed": 48000},
    )
    ambiguous_result = ambiguous.get("result") or {}
    citations = ambiguous_result.get("escalation_citations", [])
    check(
        "ambiguous procedure escalates with a cited rule",
        ambiguous_result.get("outcome") == "ESCALATE" and bool(citations),
        str([c["rule_id"] for c in citations]),
    )
    check(
        "escalation citation carries the rule text, not a generic message",
        bool(citations and citations[0].get("situation") and citations[0].get("agent_action")),
    )

    print("\nGuardrails")
    for name in ("record_decision", "approve_case", "finalise_authorisation"):
        response = client.tool(name, {})
        check(f"no {name} tool exists",
              (response.get("error") or {}).get("code") == "TOOL_NOT_FOUND",
              str((response.get("error") or {}).get("code")))

    logged = client.tool(
        "log_transcript",
        {"summary": "Deployment verification: arthroscopy request, recommendation prepared for sign-off.",
         "outcome_communicated": "RECOMMENDATION_PREPARED", "verification_id": verification_id,
         "case_reference": case_reference},
    )
    check("log_transcript returns a reference",
          bool(logged.get("ok")) and logged["result"]["reference"].startswith("CL-"),
          str((logged.get("result") or {}).get("reference")))

    status, _ = client.staff("POST", f"/api/v1/review/cases/{case_id}/assignment", actor=REVIEWER)
    check("reviewer can self-assign", status == 200, f"got HTTP {status}")

    status, body = client.staff("GET", f"/api/v1/cases/{case_id}/recommendation", actor=REVIEWER)
    recommendation_id = (body or {}).get("id", "")
    decision = {"recommendation_id": recommendation_id, "decision": "APPROVE",
                "rationale": "Deployment verification: criteria met and documentation complete."}
    status, body = client.staff("POST", f"/api/v1/review/cases/{case_id}/decision", decision, actor=REVIEWER)
    blocked = status == 409 and (body.get("error") or {}).get("code") == "CALL_RECORD_PENDING"
    check("sign-off blocked until the call transcript is logged", blocked,
          str((body.get("error") or {}).get("code")))

    print("\nPost-call webhook")
    if assemblyai_webhook_secret:
        # A deployment check has no real provider session/timeline to retrieve. Prove the signed trust boundary;
        # actual ingestion is exercised by the offline fake-provider suite and the live acceptance call.
        payload = {"type": "session.completed", "session_id": "verify_no_live_session"}
        raw = json.dumps(payload).encode()
        status, _ = client.request(
            "POST",
            "/api/v1/voice/assemblyai/post-call",
            raw=raw,
            headers={"X-AAI-Signature": sign_assemblyai(raw, "wrong-secret", int(time.time()))},
        )
        check("webhook rejects a bad signature", status == 401, f"got HTTP {status}")
        malformed = json.dumps({"type": "session.completed"}).encode()
        status, body = client.request(
            "POST",
            "/api/v1/voice/assemblyai/post-call",
            raw=malformed,
            headers={
                "X-AAI-Signature": sign_assemblyai(
                    malformed, assemblyai_webhook_secret, int(time.time())
                )
            },
        )
        check(
            "webhook accepts its signing secret and validates the event",
            status == 400 and (body.get("error") or {}).get("code") == "WEBHOOK_PAYLOAD_INVALID",
            f"got HTTP {status}",
        )
        status, _ = client.staff(
            "POST",
            f"/api/v1/cases/{case_id}/closure",
            {"reason": "WITHDRAWN_BY_PROVIDER", "note": "Deployment verification cleanup."},
            actor={"X-Actor-Type": "SYSTEM", "X-Actor-Id": "verify-script"},
        )
        check("verification case cleaned up without bypassing sign-off", status == 200, f"got HTTP {status}")
    else:
        print("  SKIP  post-call webhook (PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET not set)")
        print("        Reviewers stay blocked on voice cases until the webhook is configured.")
        status, _ = client.staff(
            "POST",
            f"/api/v1/cases/{case_id}/closure",
            {"reason": "WITHDRAWN_BY_PROVIDER", "note": "Deployment verification cleanup."},
            actor={"X-Actor-Type": "SYSTEM", "X-Actor-Id": "verify-script"},
        )
        check("verification case cleaned up without bypassing sign-off", status == 200, f"got HTTP {status}")

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print(f"Deployment looks good. Verification case {case_reference} was cleaned up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
