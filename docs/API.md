# API Reference

_Generated from the OpenAPI specification by `scripts/export_api_docs.py`. Do not edit by hand._

Backend for provider pre-authorisation requests: case intake, validation, rule evaluation, advisory
recommendations, human review, and an immutable audit trail.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can move a case
to `APPROVED` or `DENIED`.

**Authentication assumption:** requests arrive through a gateway that authenticates the caller and sets
`X-Actor-Type`, `X-Actor-Id`, and (for reviewers) `X-Actor-Roles`. When the service is exposed on a public URL
without such a gateway, set `PREAUTH_GATEWAY_SECRET`; every `/api/v1` route except the voice channel then also
requires a matching `X-Gateway-Secret` header. Voice-channel routes authenticate separately (bearer token for
tool calls, HMAC signature for the post-call webhook).

Every response carries `X-Request-ID`. Errors use a uniform envelope: `{"error": {"code", "message", "details",
"request_id", "case_id"}}`.

Schemas referenced below are defined in [`openapi.json`](openapi.json) under `components.schemas`.

## Provider interaction

### `GET /api/v1/cases/by-reference/{case_reference}` — Look up a case by its reference

Resolves a caller-quoted case reference (e.g. `PA-7K3M9Q2B`).

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_reference` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}` — Retrieve a case

Returns all collected information, documents, and current status.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/documents` — Register a supporting document

Registers metadata for a document already placed in the document store (`storage_uri`). File upload itself is handled by the document store, not this service.

**Authorisation:** Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`.

**State transitions:** Same as information update: editable states → `INFORMATION_COLLECTION`.

**Parameters:** `case_id` (path)

**Request body:** `RegisterDocumentCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 201 | `DocumentView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | INTAKE_ACTOR_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_EDITABLE / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}/required-information` — Retrieve required and missing information

Lists intake requirements and what is still missing. When intake is complete the ruleset is run as a side-effect-free dry run to surface rule-level needs (e.g. document types). `source` distinguishes information the provider can supply from gaps the insurer must resolve.

**Authorisation:** Any authenticated actor.

**State transitions:** None. No audit event.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `RequiredInformationView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}/status` — Retrieve case status

Current status, review queue, statuses reachable from here, and the final human decision if one exists.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseStatusView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}/recommendation` — Retrieve the latest recommendation

The most recent system recommendation with its rule results, evidence, rationale, and engine versions. `advisory_only` is always true: a recommendation is not an authorisation decision.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `RecommendationView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CASE_NOT_FOUND / RECOMMENDATION_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/closure` — Close a case

`WITHDRAWN_BY_PROVIDER` before review; `DECISION_COMMUNICATED` after a human decision.

**Authorisation:** Withdrawal: `PROVIDER_PORTAL` or `VOICE_AGENT`. After a decision: `SYSTEM` or `HUMAN_REVIEWER`.

**State transitions:** `RECEIVED` / `INFORMATION_COLLECTION` / `PENDING_INFORMATION` / `RECOMMENDATION_READY` / `APPROVED` / `DENIED` → `CLOSED` (terminal).

**Parameters:** `case_id` (path)

**Request body:** `CloseCaseCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | TRANSITION_NOT_AUTHORIZED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | INVALID_CLOSE_REASON / INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/documents/upload` — Upload and register a supporting document

Receives up to 10 MB of PDF, PNG or JPEG bytes and registers their hash and metadata in private storage.

**Authorisation:** Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`.

**State transitions:** Editable states → INFORMATION_COLLECTION.

**Parameters:** `case_id` (path), `document_type` (query), `title` (query)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 201 | `DocumentView` | Successful Response |
| 422 | `HTTPValidationError` | Validation Error |

### `GET /api/v1/cases/{case_id}/documents/{document_id}/content` — Download a registered supporting document

Returns privately stored content attached to this case as a download.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path), `document_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | — | Successful Response |
| 422 | `HTTPValidationError` | Validation Error |

## Audit

### `GET /api/v1/cases/{case_id}/audit-events` — Retrieve the audit trail

Immutable, ordered audit events for the case. Events are append-only at the database level.

**Authorisation:** Actor type `HUMAN_REVIEWER` or `SYSTEM`.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of `AuditEventView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Human review

### `GET /api/v1/review/queues/{queue}` — List a review queue

Cases awaiting review in the queue; expedited first, then oldest first.

**Authorisation:** Actor type `HUMAN_REVIEWER`.

**State transitions:** None.

**Parameters:** `queue` (path), `limit` (query)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of `ReviewQueueItemView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/review/cases/{case_id}` — Retrieve the review packet

Everything a reviewer needs: case details, provider request, collected information, documents, current and historical recommendations with rule results and evidence, prior decisions, and full audit history.

**Authorisation:** Actor type `HUMAN_REVIEWER`.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `ReviewPacketView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/review/cases/{case_id}/assignment` — Assign the case to yourself

Self-assignment. A decision can only be recorded by the assigned reviewer. Reassignment is allowed and audited with the previous assignee.

**Authorisation:** Actor type `HUMAN_REVIEWER` with role `CLINICAL_REVIEWER` (case `PENDING_HUMAN_REVIEW`) or `MEDICAL_DIRECTOR` (case `ESCALATED`).

**State transitions:** None (records `REVIEWER_ASSIGNED`).

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseStatusView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED / INSUFFICIENT_REVIEWER_ROLE |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_UNDER_REVIEW / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/review/cases/{case_id}/decision` — Record a human decision

Records the reviewer's decision against the current recommendation (`recommendation_id` must be the latest; guards against deciding on stale information). A rationale is mandatory. The system recommendation is never modified; departures are flagged `is_override` and audited as `RECOMMENDATION_OVERRIDDEN`.

**Authorisation:** Actor type `HUMAN_REVIEWER` with role `CLINICAL_REVIEWER` (case `PENDING_HUMAN_REVIEW`) or `MEDICAL_DIRECTOR` (case `ESCALATED`). Must be the assigned reviewer.

**State transitions:** `PENDING_HUMAN_REVIEW` → `APPROVED` / `DENIED` / `PENDING_INFORMATION` / `ESCALATED`; `ESCALATED` → `APPROVED` / `DENIED` / `PENDING_INFORMATION`.

**Parameters:** `case_id` (path)

**Request body:** `HumanDecisionCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 201 | `ReviewDecisionView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED / INSUFFICIENT_REVIEWER_ROLE / REVIEWER_NOT_ASSIGNED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_UNDER_REVIEW / STALE_RECOMMENDATION / INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/review/callbacks` — List human callback requests

Callers the voice agent handed to a human (ambiguous or non-standard requests, supplier enquiries, complaints, urgent concerns, unsupported languages). Oldest first.

**Authorisation:** Actor type `HUMAN_REVIEWER`.

**State transitions:** None.

**Parameters:** `status` (query), `limit` (query)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of `CallbackView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/review/callbacks/{callback_id}/resolution` — Resolve a callback request

Marks a callback as handled with a note. Records `HUMAN_CALLBACK_RESOLVED` on the linked case, if any.

**Authorisation:** Actor type `HUMAN_REVIEWER`.

**State transitions:** None (callback `OPEN` → `RESOLVED`).

**Parameters:** `callback_id` (path)

**Request body:** `ResolveCallbackCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CallbackView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 404 | `ErrorResponse` | CALLBACK_NOT_FOUND |
| 409 | `ErrorResponse` | CALLBACK_ALREADY_RESOLVED |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Voice agent tools

### `GET /api/v1/agent/tools` — List agent tool definitions

Tool names, descriptions, and JSON Schemas for inputs and outputs, suitable for registering with a tool-calling model. Contains no decision-making tools.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/agent/tools/{tool_name}` — Invoke an agent tool

Invokes one tool. The body is the tool's arguments object and is validated strictly against that tool's `input_schema` (see `GET /api/v1/agent/tools`); the response matches its `output_schema`. Errors use the standard envelope and HTTP status of the underlying operation.

**Authorisation:** Actor type `VOICE_AGENT` only.

**State transitions:** Those of the underlying operation (see the corresponding provider-interaction endpoint).

**Parameters:** `tool_name` (path)

**Request body:** JSON object (see tool `input_schema` / `output_schema`)

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID |
| 403 | `ErrorResponse` | VOICE_AGENT_REQUIRED / TRANSITION_NOT_AUTHORIZED |
| 404 | `ErrorResponse` | TOOL_NOT_FOUND / CASE_NOT_FOUND / RECOMMENDATION_NOT_FOUND |
| 409 | `ErrorResponse` | INVALID_STATE_TRANSITION / CASE_NOT_EDITABLE / RECOMMENDATION_NOT_READY / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED / TOOL_ARGUMENTS_INVALID / UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / ... |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Voice channel diagnostics

### `POST /api/v1/voice/tools/{tool_name}` — Invoke a voice tool for deployment diagnostics

Invokes one provider-neutral voice tool and links the optional `X-Conversation-ID` to any case it touches. This endpoint is used by deployment verification; live AssemblyAI sessions execute the same gateway directly over their server-owned WebSocket.

**Authorisation:** Header `Authorization: Bearer <PREAUTH_VOICE_TOOL_TOKEN>`.

**State transitions:** Those of the underlying tool. Never `APPROVED` or `DENIED`.

**Parameters:** `tool_name` (path)

**Request body:** JSON object (see tool `input_schema` / `output_schema`)

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `VoiceToolResponse` | Successful Response |
| 401 | `ErrorResponse` | VOICE_TOKEN_INVALID |
| 422 | `HTTPValidationError` | Validation Error |
| 500 | `ErrorResponse` | INTERNAL_ERROR |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED |

## Voice channel (AssemblyAI)

### `POST /api/v1/voice/assemblyai/post-call` — AssemblyAI completed-session webhook

Receives signed `session.completed` notifications, fetches the authoritative session timeline artifact, stores a normalized immutable call record, and links it to every case touched during that session. Returns a retryable 503 while the timeline artifact is still being prepared.

**Authorisation:** Header `X-AAI-Signature` (timestamped HMAC-SHA256 with `PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET`).

**State transitions:** None. Human sign-off on affected cases becomes possible only after the transcript is recorded.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `PostCallOutcome` | Successful Response |
| 400 | `ErrorResponse` | WEBHOOK_PAYLOAD_INVALID |
| 401 | `ErrorResponse` | WEBHOOK_SIGNATURE_INVALID |
| 422 | `HTTPValidationError` | Validation Error |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / WEBHOOK_ARTIFACT_PENDING / VOICE_PROVIDER_UNAVAILABLE |

## Voice channel (Twilio inbound)

### `POST /api/v1/voice/twilio/inbound` — Twilio incoming-call webhook

Set as the Voice webhook of your Twilio number (A call comes in → Webhook → HTTP POST). Accepts Twilio's form-encoded call parameters and returns `application/xml` TwiML containing a signed, short-lived AssemblyAI media WebSocket URL.

**Authorisation:** Header `X-Twilio-Signature`, validated with `TWILIO_AUTH_TOKEN` against `PREAUTH_PUBLIC_BASE_URL` + this path. Provider-specific credentials and agent IDs must also be configured.

**State transitions:** None. The call uses the AssemblyAI business tools and transcript safety boundary.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | — | TwiML for Twilio |
| 400 | `ErrorResponse` | TWILIO_CALL_INVALID (From or To missing) |
| 401 | `ErrorResponse` | TWILIO_SIGNATURE_INVALID |
| 422 | `HTTPValidationError` | Validation Error |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED |

## Voice channel (local)

### `GET /api/v1/local/capabilities` — Which local model and speech engines this process is using

Reports the configured local LLM, speech recogniser and speech synthesiser, so the browser knows whether to offer the microphone and whether to expect audio back.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** None.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

### `GET /api/v1/local/diagnostics` — What is still missing before a local call can be taken

The same checks as `scripts/check_local.py`, over HTTP. Each failing check carries the command that fixes it.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** None.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

### `POST /api/v1/local/conversations` — Start a local call

Opens a conversation and returns the agent's opening line, with audio when speech synthesis is configured. The `conversation_id` is passed to every tool call, so the case this call touches cannot be signed off until the call is finished and its transcript stored.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** None.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

### `POST /api/v1/local/conversations/{conversation_id}/text` — Send a typed caller turn

Runs one caller turn through the local model, which may call `verify_caller`, `check_coverage_rule` or `log_transcript`. Returns the agent's reply, the tool calls it made, and the state of the case.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** Those of the tools the agent calls. Never `APPROVED` or `DENIED`.

**Parameters:** `conversation_id` (path)

**Request body:** JSON object (see tool `input_schema` / `output_schema`)

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

### `POST /api/v1/local/conversations/{conversation_id}/audio` — Send a spoken caller turn

Transcribes the request body with the local speech recogniser, then handles the result exactly as a typed turn. The body is the raw recording (the browser posts a `webm/opus` blob straight from MediaRecorder); `wav`, `mp3` and `ogg` work too. Unintelligible or empty audio is rejected with `SPEECH_NOT_RECOGNISED` rather than guessed at.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** Those of the tools the agent calls. Never `APPROVED` or `DENIED`.

**Parameters:** `conversation_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

### `GET /api/v1/local/conversations/{conversation_id}` — Transcript and case state so far

The turns taken, the tools called, and the current case and human-review status.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** None.

**Parameters:** `conversation_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

### `POST /api/v1/local/conversations/{conversation_id}/finish` — End the call and store its transcript

Writes the conversation to the immutable `call_records` table and adds a `CALL_RECORDED` audit event to every case it touched. Until this runs, reviewers see `CALL_RECORD_PENDING` on those cases and cannot sign them off. Idempotent.

**Authorisation:** None by default: local mode binds to 127.0.0.1. When `PREAUTH_GATEWAY_SECRET` is set, these routes require a matching `X-Gateway-Secret` header like every other route.

**State transitions:** None directly; it unblocks human sign-off on the cases the call touched.

**Parameters:** `conversation_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | GATEWAY_SECRET_INVALID |
| 404 | `ErrorResponse` | CONVERSATION_NOT_FOUND |
| 409 | `ErrorResponse` | CONVERSATION_CLOSED |
| 422 | `ErrorResponse` | SPEECH_NOT_RECOGNISED |
| 503 | `ErrorResponse` | CHANNEL_NOT_CONFIGURED / LOCAL_DEPENDENCY_MISSING |

## Operations

### `GET /health` — Liveness check

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |

