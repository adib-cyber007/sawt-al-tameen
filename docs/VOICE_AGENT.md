# Voice agent (ElevenLabs)

How the ElevenLabs agent connects to this backend, what the setup script configures, and what you configure in the
dashboard. For the same agent running entirely on your own machine — Whisper, Ollama and Piper instead of
Scribe, a hosted model and Eleven v3, over the same three tools — see [LOCAL_MODE.md](LOCAL_MODE.md).

```
 caller (phone via Twilio, or browser)
        │  audio
        ▼
 ElevenLabs agent ── Scribe STT + keyterms ── LLM (+ fallback) ── Eleven v3 TTS ── Knowledge base (RAG)
        │                                                                          knowledge_base/
        │  server tools: POST /api/v1/voice/tools/{tool}   (Bearer token, X-Conversation-ID)
        ▼
 this backend ── catalogue · rules · recommendations · audit ── human reviewers (/api/v1/review/...)
        ▲
        │  post-call webhook: POST /api/v1/voice/elevenlabs/post-call   (HMAC signed transcript + analysis)
 ElevenLabs
```

## Identity and terminology

The agent is the in-house pre-authorisation line for **Sawt Assurance**, a fictional UAE insurer. It administers
pre-authorisation itself rather than through a third-party administrator, and says so if asked, so there is one
consistent story on the call.

Terminology follows UAE practice (see the research notes in the project report):

- **Pre-authorisation / prior authorisation** for the request; **authorisation reference** is avoided in favour of
  the concrete case reference the tools return.
- **Turnaround:** elective outpatient within six working hours, elective inpatient within 24 hours, emergencies
  immediately with written confirmation within 24 hours. These mirror the DHA claims directive in force in 2026.
- **Document channels:** the provider portal, or **eClaimLink** (Dubai) and **Shafafiya** (Abu Dhabi).
- **Emirates ID** is the identifier UAE providers normally use for eligibility; this line verifies with the policy
  number plus date of birth, and the member records carry an Emirates ID for future use.
- "Letter of guarantee" / "LOG" is **not** used: research did not confirm it as standard UAE insurer usage, so the
  agent says "case reference" instead of risking a wrong term.

## How the guardrails are enforced

1. **No decision tool exists.** Three tools, none of which approves, denies or finalises
   (`tests/integration/test_agent_tools.py`).
2. **Verification gates coverage.** `check_coverage_rule` requires a `verification_id` from a successful
   `verify_caller`, so a greeting-stage or unverified call cannot obtain a coverage answer.
3. **Escalations cite a rule.** Ambiguous cases return the ESC-### rule from `knowledge_base/escalation_rules.json`
   with its text, not a generic "needs review".
4. **Sources are real.** Coverage answers cite the tier's schedule and section, which exist as documents in the
   agent's knowledge base.
5. **Transcripts precede sign-off.** A case touched by a call cannot be decided until its transcript is stored.

All five hold for the local channel too, because all five are enforced below the voice provider.

## 1. Run the setup script

Prerequisite: the backend is running on a public HTTPS URL with `PREAUTH_VOICE_AGENT_TOKEN` set (see
[DEPLOYMENT.md](DEPLOYMENT.md)), and the catalogue loaded (`python -m preauth.seed`).

```bash
export ELEVENLABS_API_KEY=...            # ElevenLabs → Developers → API keys
export PREAUTH_PUBLIC_BASE_URL=https://your-backend.example
export PREAUTH_VOICE_AGENT_TOKEN=...     # the same value the backend uses

uv run python scripts/elevenlabs_setup.py --dry-run   # inspect what will be sent
uv run python scripts/elevenlabs_setup.py
```

It creates or updates, in one run:

- a workspace secret holding `Bearer <token>`;
- the three webhook tools, with schemas generated from the backend's own input models;
- the knowledge base: every file in `knowledge_base/` (tiers, procedures, providers, members, onboarding, the four
  per-tier schedules, and the escalation rules);
- the agent: English-only system prompt from `voice/system_prompt.md`, English first message, the `end_call`
  system tool, Eleven v3 conversational TTS, and 100 speech
  keyterms (procedure codes, tier and network names, provider numbers, identifier prefixes).

IDs are kept in `.elevenlabs-state.json`, so re-running updates in place. Options: `--llm`, `--tts-model`
(use `eleven_flash_v2_5` if v3 conversational is unavailable on your plan) and `--voice-id`.

> The script follows the ElevenLabs API reference as of September 2026 but has not been run against a live account
> from this repository. If a request is rejected it prints ElevenLabs' error body; the dashboard steps below are
> the fallback.

## 2. Dashboard configuration

### Post-call webhook (required before any sign-off)

1. Workspace settings → Webhooks → create an HMAC webhook pointing at
   `https://<your-backend>/api/v1/voice/elevenlabs/post-call`.
2. Copy the signing secret into `PREAUTH_ELEVENLABS_WEBHOOK_SECRET` and restart the backend.
3. In the agent's post-call webhook setting, select it and enable transcription events.

Without this, reviewers get `CALL_RECORD_PENDING` on every case the agent touched. That is deliberate.

### Workflow and per-node tool scoping

Build this in Agent → Workflow. The greeting node has no access to `check_coverage_rule`, which is also enforced in
the backend by the verification requirement.

| Node | Purpose | Tools |
|---|---|---|
| Greeting & triage | Identify caller type; route callers who cannot continue in English | none |
| Verification | Organisation, provider number, member policy and date of birth | `verify_caller` |
| Request intake | Collect procedure, cost, date; read back for confirmation | none |
| Rules check | Check the request; explain documents or escalation | `check_coverage_rule` |
| Close & log | Record the outcome, read back references | `log_transcript`, `end_call` |
| Human handoff | Supplier, complaint, unsupported language, repeated failure | `log_transcript`, `end_call` |

Edges: greeting → verification for clinics and brokers; greeting → human handoff for suppliers, patients and
out-of-scope calls; verification → request intake only when `authorised` is true, otherwise → human handoff; rules
check → close & log in all cases.

### Language, LLM fallback and analysis

- English is the only automated voice language. Callers who cannot continue in English are routed to a human
  callback; the agent must not attempt to translate or continue the pre-authorisation flow in another language.
- Enable a backup model (LLM cascading) so a primary-model timeout does not drop a live call.
- Evaluation criteria to add under Analysis:
  - `no_decision_given` — never said or implied approved/denied, never disclosed an internal recommendation outcome.
  - `identifiers_confirmed` — read back the policy number, procedure code, amount and date before checking.
  - `verification_before_policy_detail` — discussed no policy detail before `verify_caller` returned authorised.
  - `correct_routing` — complete requests prepared for sign-off; ambiguous ones escalated with a reason.
  - `reference_given` — caller received the case reference or call reference.
- Data collection: `case_reference`, `call_reference`, `caller_role`, `call_language`, `procedure_code`.

### Agent tests

`voice/agent_tests.json` holds five ready-made definitions matching the scenarios the backend already proves:
high-stakes refusal, clean approval recommendation, ambiguous escalation, lapsed member, and an unsupported-language
callback. Each lists expected and forbidden tool calls. Create them under Agent → Tests and run each several
times for a pass rate.

### Voice

`--voice-id` defaults to a neutral, professional Voice Library voice rather than a consumer-warm one. Before
finalising, preview it in your workspace on a spoken policy number and an SP-code sequence
("P-O-L dash S-A dash 2026 dash 100001", "S-P-2-0-0-4-0") and confirm the digits are crisp. That check needs your
account; it cannot be done from this repository.

## 3. Talk to it

- **Browser (free):** `https://elevenlabs.io/app/talk-to?agent_id=<agent_id>`, printed by the setup script.
- **Phone:** see [DEPLOYMENT.md → Phone numbers](DEPLOYMENT.md#phone-numbers-what-is-and-isnt-free).

Synthetic data to use on a call:

| Item | Values |
|---|---|
| Providers | `PRV-30011` Al Hudaiba Crescent Hospital (Basic network, orthopaedics), `PRV-30023` Yas Horizon (Comprehensive, bariatric), `PRV-30020` Mirdif Vision (suspended), `PRV-30030` Gulf Meridian (Executive only) |
| Members | `POL-SA-2026-100001` / 1986-04-17 (Executive), `POL-SA-2026-100003` / 1991-07-29 (Basic), `POL-SA-2026-100011` / 1981-05-02 (Comprehensive), `POL-SA-2026-100008` / 1990-12-04 (lapsed) |
| Procedures | `SP-20040` arthroscopy (covered, needs 3 documents), `SP-20050` knee replacement (Enhanced and above), `SP-20110` sleeve gastrectomy (escalates, ESC-001), `SP-20140` cosmetic rhinoplasty (excluded), `SP-10010` chest X-ray (no pre-auth needed) |
| Onboarding | `ONB-APP-2026-0007` (two documents outstanding) |
