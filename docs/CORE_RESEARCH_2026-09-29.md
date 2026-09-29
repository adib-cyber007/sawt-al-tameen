**Sawt al-Tameen: research on improving the core voice and pre-authorisation workflow**

Research date: 29 September 2026. Based on the current working tree, including its uncommitted changes, and public primary sources from AssemblyAI, Twilio, LiveKit, Pipecat, and UAE health authorities.

**Recommendation:** retain the deterministic coverage engine, human decision boundary, and managed AssemblyAI integration. First correct the Twilio transport contract, make tool execution safe under slow responses and interruptions, improve identifier capture, and measure audible latency. Then invest in document completion and reviewer workflow. Those changes have a more direct connection to project impact than changing the LLM without a comparative evaluation.

This is a research assessment, not a live production certification. No paid calls, provider changes, deployments, or application code changes were made. Existing test counts and live observations below are repository records, not tests rerun for this assessment. The local Python launcher could not start; the keyterm count was independently reproduced with PowerShell from the source algorithm and catalogue. Several AssemblyAI documentation pages were available through search-indexed text but failed direct retrieval; disagreements between official pages are identified below. Suggested performance targets are proposals, not measured results or vendor guarantees.

**What the project actually connects**

| Path | Current implementation | Consequence for improvement |
|---|---|---|
| Phone | Twilio inbound webhook → application Media Streams bridge → AssemblyAI stored phone agent, PCMU at 8 kHz | Audio, authentication, playback tracking, and transfer behavior need telephony-specific acceptance. |
| Browser | Microphone → capture AudioWorklet → backend WebSocket → AssemblyAI browser agent, PCM16 at 24 kHz → playback AudioWorklet | Device processing and local buffering materially affect what the user hears. |
| Hosted reasoning | Provisioning explicitly sends `llm: []` to use AssemblyAI's managed conversational model | There is no separately configured frontier LLM in the current hosted path. Changing a standalone LLM setting elsewhere will not upgrade this path. |
| Local reasoning | Faster-whisper → Ollama, default `qwen2.5-coder:7b` → Piper | Useful for offline development, but it needs its own accuracy and latency baseline. |
| Business operations | `verify_caller`, `check_coverage_rule`, `log_transcript` → application services → rules/recommendations/audit | Keep the LLM responsible for conversation and extraction; retain explicit application authority over case state. |
| Review | Reviewer-only decision service, with a durable transcript prerequisite | Measure whether calls produce review-ready cases and whether reviewers can act without repeating intake. |

AssemblyAI describes its Voice Agent API as a managed speech-in/speech-out service, distinct from using streaming STT with a separately selected LLM and TTS. This distinction matters when comparing cost and control. [AssemblyAI architecture options](https://www.assemblyai.com/solutions/voice-agents)

The existing project already implements valuable protections: tools without final-decision authority, a deterministic rules engine, source citations, append-only records, signed incoming webhooks, separate browser/phone audio formats, session resumption, and post-call reconciliation. Preserve these while improving the surrounding workflow.

**Repository evidence used in this assessment**

| Evidence | Location |
|---|---|
| Agent configuration, managed LLM, keyterms and voice focus | [assemblyai_setup.py](../scripts/assemblyai_setup.py#L35) |
| Twilio stream URL construction | [twilio_inbound_service.py](../src/preauth/application/twilio_inbound_service.py#L83) |
| Browser and phone WebSocket admission | [assemblyai.py](../src/preauth/api/routes/assemblyai.py#L50) |
| Tool execution, interruption handling, reconnects, media forwarding | [assemblyai_bridge.py](../src/preauth/api/assemblyai_bridge.py#L58) |
| Browser playback reserve | [pcm-playback.js](../src/preauth/hosted_web/pcm-playback.js#L1) |
| Microphone processing | [app.js](../src/preauth/hosted_web/app.js#L143) |
| Verification and case creation | [desk_service.py](../src/preauth/application/desk_service.py#L204) |
| Tool input schemas | [toolbox.py](../src/preauth/agent_tools/toolbox.py#L73) |
| Human decision and review packet | [review_service.py](../src/preauth/application/review_service.py#L116) |
| Post-call normalization and metrics | [assemblyai_post_call.py](../src/preauth/application/assemblyai_post_call.py#L115) |
| Identity trust boundary | [actor.py](../src/preauth/api/actor.py#L23) |
| Spoken instructions and live scenarios | [system_prompt.md](../voice/system_prompt.md), [agent_tests.json](../voice/agent_tests.json) |
| Existing acceptance evidence and known limitations | [MIGRATION_LOG.md](../MIGRATION_LOG.md), [ARCHITECTURE.md](../docs/ARCHITECTURE.md) |

**Prioritized improvements**

Implementation update: recommendations 1–3 are implemented on `codex/voice-reliability`; see
[voice reliability](VOICE_RELIABILITY.md) for the changes, automated validation, and remaining live acceptance.
The findings below describe the original research baseline.

P0 means a correctness or access-control issue to resolve before relying on that capability with external users. P1 means a high-value quality or reliability improvement. P2 means a larger product investment. Effort is relative: small is a contained change, medium spans several components, and large requires integrations or operational work. These are engineering judgments, not delivery estimates.

1. **Correct the Twilio stream authentication transport. P0; small–medium effort.**

   The current TwiML puts a signed token in `wss://.../twilio?token=...`, and the WebSocket route requires that query parameter. Twilio explicitly says the `<Stream>` URL does not support query strings and provides nested `<Parameter>` values instead. This is a documented protocol mismatch; no actual telephone failure was reproduced in this research. [Twilio Stream contract](https://www.twilio.com/docs/voice/twiml/stream#url)

   Pass the short-lived, CallSid-bound token through a custom parameter. Validate Twilio's WebSocket upgrade signature, then accept only long enough to receive and validate the `start` message and its custom parameters. Enforce a short start deadline and frame limits; do not open a billable AssemblyAI session or execute tools before verification succeeds. Keep inbound webhook signature validation. Twilio requires signature validation for Media Streams connections. [Media Streams security](https://www.twilio.com/docs/voice/media-streams)

   Acceptance: a real inbound call completes intake; invalid signature, expired token, replay, missing start, and CallSid mismatch all fail without creating an upstream session. Offline tests must model the actual custom-parameter contract.

2. **Remove slow-tool waits from the provider receive loop. P0 for reliable interruption handling; medium effort.**

   Tools already start asynchronously, which is good. However, `AssemblyAIToolCoordinator.handle()` awaits `asyncio.gather(*ready)` on `reply.done`, and the provider receive loop awaits that handler. A tool still running at that boundary can prevent the loop from reading subsequent speech-start, audio, and error events.

   Use a separate result dispatcher, tracked turn generations, and explicit safe-send state. Preserve sequential business operations where required. Give each tool a deadline and keep receiving media while it runs. AssemblyAI's Twilio example specifically handles tools completing on either side of `reply.done` without blocking the media pump. [AssemblyAI Twilio example](https://github.com/AssemblyAI-Solutions/voice-agent-api-twilio-example)

   Acceptance: inject a three-second lookup delay after the transition phrase finishes; a caller interruption must still be processed promptly, and the result must never be attached to the wrong turn. Confirm event timing against the deployed API before changing it; official sources disagree, as discussed later.

3. **Make tool side effects idempotent and interruption-aware. P0; medium effort.**

   Clearing `_pending` on interruption suppresses results but does not stop an already running thread or reverse database commits. A repeated coverage tool call without `case_reference` creates another case. The gateway does not receive the provider `call_id`, so that identifier currently cannot deduplicate the business operation.

   Persist execution state keyed by session and provider call id, with request hashes and stored results. Also assign an application request identifier: the model can retry the same business operation with a new provider call id. Resolve retries to the existing case, while allowing an explicit new request. Commit case creation and the invocation record atomically where possible; model the multi-transaction evaluation/review sequence as recoverable steps.

   Acceptance: interruption after commit, timeout after commit, reconnect, and repeated requests do not create duplicate cases or callbacks. Cancellation of audio generation must never be mistaken for cancellation of a completed business transaction.

4. **Optimize audible browser latency with measured adaptive buffering. P1; medium effort.**

   Playback currently starts after three seconds of audio have accumulated, after a shorter reply completes, or after a five-second wait at the queue head with audio available. Three seconds of audio is not necessarily three seconds of wall time, but this policy can materially delay the first audible response. The implementation comments describe long delivery gaps, while older migration observations measured much shorter gaps; the current distribution needs fresh measurement.

   Instrument first packet arrival, first sample rendered, underruns, queue depth, and reply completion. Experiment with smaller reserves on stable connections and raise the reserve after observed underruns. Keep the existing larger reserve as a degraded-network option. Do not simply reduce the constant without testing the two-second-gap case that motivated it.

   Acceptance: compare median and p95 time from caller finishing to audible response, together with underrun frequency and listening ratings. Provider time-to-first-audio alone excludes local playback delay. Pipecat's separate processing, first-byte, and first-audio metrics provide a useful instrumentation model. [Pipecat metrics](https://docs.pipecat.ai/pipecat/fundamentals/metrics)

5. **Fix the truncated keyterm list and make vocabulary stage-specific. P1; small–medium effort.**

   Reproducing the current generator gives **140 unique terms, truncated to 100**. Only **20 of 50 procedure codes** survive. **All ten appended medical terms** are discarded. Consequently, adding clinical terms at the end of the list currently has no effect on the provisioned payload. This is a configuration finding, not proof that the dropped terms are always mistranscribed.

   Allocate vocabulary by stage: provider names during caller identification; formatting context during member verification; procedure names and codes during request collection. Rank terms by observed confusion and frequency rather than catalogue insertion order. Update `session.input.keyterms` through the Voice Agent API; do not copy the raw streaming API's different `keyterms_prompt`/`UpdateConfiguration` syntax. Never bias recognition toward an unverified member's secret value. [Voice Agent session configuration](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/session-configuration)

   Acceptance: snapshot which terms reach provisioning, and measure exact field accuracy on held-out providers and procedures, including codes previously omitted.

6. **Tune recognition for identifiers and the actual microphone channel. P1; small–medium effort.**

   Both agents use `balanced` transcription and `far-field` voice focus. AssemblyAI identifies near-field as appropriate for headsets and phone handsets, and far-field for more distant room audio. Pilot near-field for telephone calls, retaining separate browser device profiles. Do not assume that one profile wins for every speakerphone call. [AssemblyAI acoustic profiles](https://www.assemblyai.com/blog/voice-agents-noisy-environments)

   Pilot `max_accuracy` while collecting policy numbers, procedure codes, dates, and AED amounts; return to `balanced` for ordinary discussion. The provider documents mid-call changes to transcription mode. Avoid blindly imposing fixed silence thresholds that could interfere with adaptive endpointing. [AssemblyAI transcription-mode release](https://www.assemblyai.com/changelog?3b7e7275_page=4)

   Acceptance: test digit pauses, corrections, quiet speech, and English spoken with Arabic and South Asian accents. Report field errors and added latency separately.

7. **Enforce confirmed intake fields in application state. P1; medium effort.**

   The prompt requires explicit confirmation, but the coverage tool schema accepts values without an application-verifiable confirmation record. A well-formed, plausible model argument can therefore pass schema validation even if the caller never confirmed it.

   Store each critical slot with raw transcript evidence, normalized value, source turn, confirmation turn, and revision. Bind a server-issued confirmation reference to the exact field values and session. Corrections invalidate the previous confirmation. A model-supplied `confirmed: true` alone is insufficient. Treat speech confidence as a prioritization signal, not proof that an identifier is correct.

   Acceptance: “fifteen thousand—sorry, fifty thousand,” ambiguous dates, and changed procedure codes cannot trigger evaluation using stale values. A whole-request exact-match score should accompany individual-field accuracy.

8. **Offer keypad and browser form recovery for hard-to-hear fields. P1; medium effort.**

   Twilio sends inbound `dtmf` events on bidirectional streams; the current bridge does not handle them. Provide keypad entry for numeric portions of identifiers, dates, and callback numbers after repeated recognition failures. Handle known alphabetic prefixes through explicit field context, not guesses. Offer typed correction and confirmation for browser callers. [Twilio DTMF messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages#dtmf-message)

   Keep both channels tied to the same verified intake state. Limit attempts and avoid logging raw sensitive keypresses in ordinary telemetry. A code's valid format does not prove the caller's identity.

   Acceptance: callers can recover from two failed voice attempts without starting over, and keypad/form submissions go through the same authorization and confirmation checks.

9. **Distinguish generated speech from delivered speech. P1; medium effort.**

   The phone bridge sends `media` and `clear` but has no `mark` handling. A provider transcript describes generated speech; queued audio may be cleared before Twilio plays it. Use named marks at useful chunk or utterance boundaries, track acknowledgements, and classify acknowledgements associated with a clear operation. Twilio returns pending marks after a clear too, so a mark alone must not be treated as proof of successful playback. [Twilio playback tracking](https://www.twilio.com/docs/voice/media-streams/websocket-messages#mark-message)

   Add equivalent browser playback acknowledgements. Preserve the provider transcript while attaching a separate delivery ledger; do not rewrite immutable history to pretend everything was heard. Reconfirm critical case references if delivery was interrupted. Playback completion still does not prove human comprehension.

   Acceptance: interrupt during a reference number; the system records the interrupted delivery and repeats or reconfirms it.

10. **Improve browser interruption without reintroducing speaker echo. P1; medium effort.**

   Browser mode currently gates the microphone during replies and disables provider interruption. This solves a real echo problem but forces callers to wait through mistakes and prevents quick corrections.

   First add an explicit stop-speaking control and a supported conversation-turn transition. Then pilot full-duplex headset mode while retaining a speaker-safe mode. Evaluate echo cancellation, browser noise suppression, automatic gain control, and provider Voice Focus as a matrix. The code enables browser noise suppression, while the voice guide contains contradictory statements about whether it is enabled; reconcile the documentation after testing.

   Acceptance: evaluate false interruptions, missed interruptions, and recovery after stopping a reply across Chrome/Edge, Firefox, Safari, headphones, and speakers. Keep acoustic echo cancellation distinct from generic noise suppression.

11. **Strengthen session admission, tenant isolation, and caller authentication. P0 before real data or broader public exposure; medium–large effort.**

   The browser WebSocket route accepts connections when configuration exists; no application-level caller authentication, origin validation, concurrency cap, or session-duration policy is visible there. The staff API trusts gateway-supplied identity headers and only checks the shared gateway secret when configured. These assumptions must be enforced by deployment, not left implicit.

   Add authenticated, short-lived browser session grants, origin validation for browser traffic, per-tenant/IP/session quotas, message-size limits, and idle/maximum durations. Origin checks complement authentication; non-browser clients can forge an Origin header. Require authenticated reviewer identities and provider-scoped object access.

   Verification records store a conversation id, but `_require_authorised_for_coverage` does not compare it with the active conversation or apply a freshness check. Existing-case checks compare member identity but not provider ownership. Bind verification to session, tenant/provider, expiry, and member, with an explicit separately authenticated re-verification path for later calls. Provider number plus member DOB/policy matching is a lookup check, not strong proof of the caller's organizational authority. Pilot portal-mediated verification or a challenge to an already registered contact.

   Acceptance: concurrent sessions cannot reuse each other's verification or cases; unauthenticated traffic cannot consume unlimited voice capacity; reviewer roles cannot be self-asserted through a public endpoint.

12. **Make transcript ingestion durable and observable. P1; medium effort.**

   The signed webhook, idempotent call record, pending-artifact response, and reconciliation script already exist. Build on them. Persist a verified event into a durable queue before acknowledging it, then retrieve the session artifact asynchronously with bounded retries, jitter, and a dead-letter queue. Schedule reconciliation as part of deployment and alert on the age of missing transcripts.

   Preserve the existing transcript-before-sign-off rule. Give staff a visible “awaiting transcript” state and a recovery action instead of an unexplained blocked decision. Distinguish complete, partial, unavailable, and reconciled records. Maintain an append-only event journal so recoverable live events are not lost on process restart; do not automatically substitute it for an authoritative completed transcript without defining completeness rules.

   Acceptance: delayed artifact publication, duplicate webhooks, process restarts, and a provider outage recover without duplicate records or silent indefinite review blockage.

13. **Provide a human transfer with context and an outage fallback. P1; medium–large effort.**

   A callback record is useful, but the three tools do not currently transfer a live call. Add an application-controlled transfer path for repeated recognition failures, unsupported language, caller requests, and service failure. Send the recipient verified identity status, confirmed fields, missing documents, cited rule, and the exact reason for escalation.

   Twilio can redirect an active call using the Calls API. A basic transfer can use a controlled update and `<Dial>`; a genuine warm transfer needs a conference or contact-center workflow with staff acceptance. Define busy, unavailable, and after-hours paths. An allowed routing action must remain separate from authorization decisions. [Twilio active-call control](https://www.twilio.com/docs/voice/api/call-resource#update-a-call)

   Acceptance: caller and staff are connected, context is present before intake resumes, and failed transfers produce a tracked callback rather than a silent disconnect. Browser callers need their own callback or support-session experience.

14. **Move public pilots to infrastructure designed for continuous calls. P1 for external pilots; medium effort.**

   The migration log records tunnel interruptions, laptop sleep, and dependence on the Windows host. Existing recovery scripts improve demonstration availability but cannot keep calls running through power loss or disconnected sleep.

   For a public pilot, use an always-on host, persistent PostgreSQL, backups with restore drills, explicit readiness, connection draining during deployment, and bounded per-worker concurrency. Protect in-flight calls from routine restarts. Preserve the current PC/ngrok setup for development if that remains the preferred local workflow.

   Benchmark routing from actual UAE caller networks. Twilio documents US1 as default and IE1/AU1 support for Media Streams; a geographically closer application alone does not establish the fastest end-to-end path or compliant residency. [Twilio Media Streams regions](https://www.twilio.com/docs/voice/media-streams)

   Acceptance: test staged concurrency levels, deploy during active synthetic calls, verify backup recovery, and prove that one upstream fault produces a controlled user outcome.

15. **Build an audio evaluation suite around this project's real task. P1; medium effort.**

   The repository contains substantial offline tests, five manual voice scenarios, and limited recorded live acceptance. Those are different from repeated end-to-end audio task evaluation. One correctly transcribed greeting does not establish reliable policy-number capture or full-call completion.

   Start with 50–100 curated scenarios and repeated runs: clean intake; each escalation family; lapsed policy; long identifiers; changed DOB; corrected amount; caller pressure; misleading instructions; missing uploads; overlap; silence; slow tools; duplicate calls; reconnect; and call termination before logging. Include both real consented/synthetic human recordings and synthesized speech. Hold out speakers and phrases, and encode a telephony test set through 8 kHz PCMU.

   Use deterministic assertions for tool arguments, state transitions, citations, and authorization. Use human review and calibrated model judges for clarity and conversation quality. LiveKit documents this separation between turn tests and complete audio simulations; its hosted simulation tooling requires a compatible integration, so it is an architectural reference rather than a drop-in test runner for this bridge. [LiveKit simulations](https://docs.livekit.io/testing/simulations/)

16. **Measure complete outcomes and tail latency. P1; medium effort.**

   Post-call normalization currently stores average confidence and average provider first-audio latency. Averages hide failed fields and unusually slow turns. Add per-turn distributions, per-field exact matches, repeat requests, interrupted replies, tool retries, handoff outcome, transcript availability, and reviewer corrections. Correlate CallSid → StreamSid → AssemblyAI session → tool invocation → case → final human review.

   Separate audible response latency from STT, model, tool, delivery, and buffering time. Only expose component timings the provider actually supplies; do not invent internal managed-model metrics. Twilio Voice Insights can help investigate transport quality, but its metrics vary by call edge and some APIs require Advanced Features. It does not replace application-level task evaluation. [Twilio call metrics](https://www.twilio.com/docs/voice/voice-insights/api/call/call-metrics-resource)

   Acceptance: every failed acceptance scenario can be localized to recognition, conversation, tool, transport, persistence, or workflow without opening raw sensitive transcripts for routine troubleshooting.

17. **Resolve spoken-policy inconsistencies and version the conversation contract. P1; small–medium effort.**

   The prompt permits saying that a recommendation to approve or decline has been prepared. The manual test assertions prohibit saying the request is approved, denied, or “likely to be either.” Those expectations need a single precise definition. Define acceptable pending-review wording and test it with callers for understanding.

   A backend that cannot approve a case still cannot guarantee that an unconstrained voice model never speaks a misleading approval sentence. Prefer short, application-produced outcome wording derived from authoritative tool results. Where the managed voice path cannot enforce exact speech before generation, measure this limitation explicitly; a post-call check cannot prevent already-spoken content.

   The prompt also includes fixed turnaround promises and emergency statements. Move those to insurer-approved, versioned policy configuration returned by tools, with a defined business calendar and effective date. The fictional catalogue's wording must not be presented as a verified UAE-wide clinical or regulatory rule. Store prompt, rule, catalogue, and agent-config versions with each session. Acceptance should cover adversarial pressure, missing sources, conflicting instructions, and policy updates.

18. **Complete the document loop and reduce repeated intake. P2; large effort, high workflow impact.**

   Documents are metadata-only today. The voice coverage tool cannot itself attach or verify uploaded content, and a newly created case starts with no documents. A caller saying “already uploaded” cannot by itself establish that the new case has those records.

   Provide secure case-bound upload/retrieval, stable document ids, validated file types, malware scanning, duplicate detection, and controlled extraction of relevant evidence. Extract candidate dates, procedure codes, and document types with provenance and reviewer confirmation. Treat document text as untrusted data, not instructions for tools. Automatically update the missing-document checklist after successful ingestion and re-evaluate when appropriate.

   Acceptance: the same case moves from missing information to review-ready without another full phone intake. Measure document-chasing contacts, completion time, and reviewer corrections rather than just OCR accuracy.

19. **Connect the case to UAE authorization transaction workflows. P2; large effort.**

   The prompt tells callers to use eClaimLink or Shafafiya, but mentioning a platform does not make this application integrated with it. Start with a scoped adapter for existing request/status/document retrieval, using external transaction identifiers to link calls to cases. Preserve jurisdiction-specific validation and human approval of outward decisions.

   DoH publishes prior-request/authorization structures and a `GetNewPriorAuthorizationTransactions` SOAP operation. This provides a concrete investigation path, while access credentials, current schema versions, onboarding, and contractual permissions still need confirmation. Do not assume the integration is a generic FHIR REST endpoint. [DoH prior authorization structure](https://www.doh.gov.ae/en/Shafafiya/dictionary/Prior-Request-Authorization), [DoH transaction service](https://www.doh.gov.ae/en/shafafiya/dictionary/webservices/GetNewPriorAuthorizationTransactions)

   Acceptance: a voice interaction resolves to the correct external transaction, duplicate submissions are prevented, and confirmed status changes are synchronized with an audit trail. Treat the older public eClaimLink provider manual as background, not sufficient evidence of today's integration contract.

20. **Make review faster and recommendations fresh at decision time. P1–P2; medium–large effort.**

   A review packet already combines recommendations, decisions, calls, callbacks, and audit events. Add a concise evidence-linked brief: confirmed request, documents present/missing, decisive rule and section, conflicts, corrections, and action needed. Any generated summary should point to transcript turns or documents, and reviewer edits should be recorded separately.

   The architecture documents a utilization race: two requests can pass before either is approved. At human decision time, revalidate eligibility, policy version, and available benefits within an appropriate transaction. Introduce reservations or another explicit concurrency policy, including expiry/release behavior and estimated-versus-final cost handling. Keep the original recommendation as historical evidence and record any superseding evaluation.

   Also evolve beyond one procedure per case when actual transaction requirements justify it: group related lines under one encounter while retaining line-level evidence and limits. Acceptance should measure reviewer minutes per case, correction rate, stale evaluations, and overlapping approvals.

21. **Use LLM comparisons to answer a specific failure, not to chase model size. P2 after a baseline; medium–large effort.**

   Preserve the managed model until a benchmark identifies a material gap in tool accuracy, correction handling, spoken grounding, latency, language, or residency. Compare the same cases under equal audio and business rules. For local mode, compare task-appropriate tool-capable models against the existing coder-model default; report hardware, quantization, context size, cold/warm latency, and tool success. Low temperature is not a correctness guarantee.

   A separately controlled streaming STT → LLM → TTS path becomes attractive when exact pre-speech validation, a required voice/language, model choice, or component routing cannot be achieved in the managed service. It also adds orchestration, monitoring, billing, and interruption complexity. AssemblyAI explicitly distinguishes those architectures. [AssemblyAI managed versus custom pipeline](https://www.assemblyai.com/docs/coding-agent-prompts)

   Acceptance: a candidate must improve the chosen task metrics without weakening authority boundaries or creating unacceptable p95 latency. Keep expensive document analysis and optional summaries outside the live response path.

22. **Treat Arabic support as a tested product extension. P2; large effort.**

   The current English-only behavior is explicit and should remain until a separate supported path is accepted. A useful first step is a reliable language-preference capture and human handoff. A later Arabic/English pilot needs native-speaker testing of names, dialects, dates, amounts, medical terms, code-switching, and spoken explanations.

   Do not infer full Arabic conversational capability from a speech-recognition language list. AssemblyAI's multilingual material discusses Arabic input, while the Voice Agent product page lists a smaller set of spoken supported languages. Verify the chosen model, output voice, and account capability before promising bilingual service. [AssemblyAI multilingual discussion](https://www.assemblyai.com/blog/multilingual-voice-agent), [Voice Agent product capabilities](https://www.assemblyai.com/products/voice-agent-api)

   Acceptance: equivalent task completion and field accuracy across accepted languages, with predictable transfer when language confidence or voice support is insufficient.

23. **Resolve data handling for the exact voice product and deployment. P0 before real health data; medium–large effort.**

   Map where raw audio, transcripts, member identifiers, tool arguments, artifacts, backups, and support logs travel and persist. UAE health-data requirements need jurisdiction-specific review before moving from this synthetic demonstration to real members; overseas cloud hosting or an EU endpoint is not itself evidence of UAE compliance. The relevant UAE law addresses health-data handling, including Article 13 on storage/transfer outside the state. Applicability, exceptions, and current authority decisions require confirmation. [Official UAE legislation source](https://uaelegislation.gov.ae/en/legislations/1209/download)

   Obtain Voice Agent-specific answers for region, subprocessors, training opt-out, artifact retention/deletion, and contractual terms. AssemblyAI's general security page and detailed product retention documentation use different scopes; do not transfer a streaming-STT retention promise to Voice Agent session artifacts without verification. [AssemblyAI security](https://www.assemblyai.com/security), [Product retention documentation](https://www.assemblyai.com/docs/data-retention-and-model-training)

   Coordinate provider retention with the transcript sign-off prerequisite: deleting artifacts before durable ingestion could block review permanently. Separate sensitive payload storage from an immutable minimal audit ledger, with controlled retention, legal holds where applicable, encryption, and access logging. An append-only database alone does not define a workable retention policy.

24. **Optimize cost per review-ready case and demonstrate impact. P1 measurement, P2 optimization; medium effort.**

   The reviewed AssemblyAI product page lists $4.50/hour, or $0.075/minute, for the managed Voice Agent API, billed by session duration. A six-minute session would therefore be $0.45 for that component at the published rate. Verify the account's actual pricing and terms; Twilio minutes, phone numbers, hosting, storage, monitoring, failed calls, transfers, and staff review add to the total. [AssemblyAI published pricing](https://www.assemblyai.com/products/voice-agent-api)

   Use: total operating cost / cases that reach the defined review-ready state. Also measure cost per completed human-reviewed case and staff minutes saved. Shorter conversations are only a win if field accuracy and completeness survive. Proper idle termination, fewer repetitions, reusable verified context, and successful first-time document collection can improve both cost and experience.

   As an illustrative calculation, six-to-four-minute sessions reduce that provider component from $0.45 to $0.30, saving $150 per 1,000 sessions. This is arithmetic, not a forecast; incomplete four-minute calls can cost more overall. Compare against the existing manual process before claiming operational savings.

**Which architecture is worth pursuing?**

| Option | When it is useful | Main tradeoff | Recommendation for this project |
|---|---|---|---|
| Improve current Twilio + managed AssemblyAI bridge | English intake, rapid iteration, existing rules and audit integration | Limited control over internal speech/reasoning stages | First choice. Most identified issues can be addressed here. |
| AssemblyAI streaming STT + chosen LLM/TTS, with Pipecat or LiveKit | Demonstrated need for model/voice choice, strict pre-speech checks, richer media control | More components and failure modes; transcript and tool semantics need revalidation | Build a limited comparison branch only after establishing baseline metrics. |
| Twilio ConversationRelay + application LLM/tools | Phone-first service that benefits from Twilio-managed speech and contact-center workflows | Changes the current speech integration and does not replace the browser path directly | Evaluate if telephone operations become dominant. |
| Local/self-hosted voice stack | Offline deployments or a substantiated hosting requirement | Hardware capacity, model quality, operational burden | Keep for development; separately validate any production use. |

Twilio ConversationRelay manages speech recognition and synthesis while the application supplies conversational logic. It is an alternative architecture, not an additional layer to stack unnecessarily on top of managed AssemblyAI speech. [ConversationRelay overview](https://www.twilio.com/docs/voice/conversationrelay)

LiveKit's audio simulations and Pipecat's component metrics are useful framework capabilities, but neither alone proves a migration will improve this particular application. Preserve the existing tools, rules, review requirements, and end-to-end correlation whichever transport is selected. [LiveKit testing lifecycle](https://docs.livekit.io/testing/), [Pipecat metrics](https://docs.pipecat.ai/pipecat/fundamentals/metrics)

**Proposed acceptance scorecard**

These are starting targets to calibrate after collecting the baseline. Use held-out callers, report sample sizes and uncertainty, and separate browser, phone, device type, noise level, and accent. Do not claim a 99% rate from a handful of calls.

| Metric | How to measure | Initial proposed gate |
|---|---|---|
| Final decision authority | Attempt voice-agent finalization through every exposed route/tool | Zero successful unauthorized transitions in the adversarial suite |
| Misleading final-decision speech | Human-reviewed audio/transcripts, with contextual wording rules | Zero observed misleading final decisions in the release suite; ongoing sampled monitoring |
| Critical-field accuracy | Exact normalized policy id, provider id, procedure code, DOB, treatment date, AED amount against ground truth | Target ≥99% after confirmation on the accepted test population; escalate unresolved values |
| Whole-request accuracy | All required fields correct together | Report independently; never infer it from average field accuracy |
| Simple-turn audible latency | Caller end-of-speech to first meaningful rendered/played response | Investigate p50 >1.5 s or p95 >3 s; track tool turns separately |
| Interruption handling | Valid speech-start/control event to playback stopped | Candidate target p95 <300 ms after the interruption event; separately measure detection delay |
| Playback quality | Underruns, repeat starts, clipped ends, listening ratings | No regression versus baseline while reducing buffering |
| Transcript readiness | Call end to complete immutable call record | Candidate target ≥99% within two minutes; alert on records still pending after five minutes |
| Tool integrity | Fault-injected retries, slow tools, interruption, reconnect | Zero duplicate business side effects in the release suite |
| Review-ready intake | Eligible calls yielding verified, confirmed, sufficiently documented cases | Establish baseline, then seek a measured improvement; report missing-document cases separately |
| Staff workload | Reviewer active minutes and additional contacts per comparable case | Pilot target: 20% less active review effort without increased corrections; hypothesis to test |
| Economics | All-in cost per review-ready and human-reviewed case | Compare with the manual baseline, including unsuccessful calls |

At six individually 99%-accurate fields, an independence illustration gives only about 94.1% probability that all six are correct. Real errors may be correlated. This is why the whole-request metric matters even when individual fields look strong.

**Suggested sequence**

| Phase | Work | Evidence required before expansion |
|---|---|---|
| First sprint | Twilio custom parameters and signatures; tool-result dispatcher; idempotency; keyterm truncation; session admission; prompt/test agreement | Real phone contract acceptance plus deterministic fault tests |
| Second sprint | Audible-latency instrumentation; buffering experiment; phone acoustic profile; confirmation state; playback marks; DTMF/form recovery | Repeatable audio baseline and a measured quality/latency comparison |
| Pilot preparation | Durable transcript queue; scheduled reconciliation; contextual transfer; concurrency/restore tests; appropriate hosting and data handling | Controlled failures produce recoverable, auditable outcomes |
| Product expansion | Secure document workflow; external transaction links; evidence-linked reviewer experience; optional language/model experiments | Fewer repeated contacts, less reviewer effort, and lower cost per successful case |

This is sequencing, not a fixed four-week delivery promise. External access, data governance, multilingual acceptance, and document integrations can dominate elapsed time.

**Research uncertainties that should become explicit validation tasks**

- AssemblyAI's events reference and maintained Twilio example advise a reply-boundary rule for tool results, while another official speech-to-speech page says to send results immediately. Preserve the current safe behavior until the account's deployed contract is confirmed; independently fix blocking and deduplication. [Events reference](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/events-reference), [conflicting speech-to-speech guide](https://www.assemblyai.com/docs/voice-agents/speech-to-speech)
- Official pages differ on which session fields can change mid-call. Verify each proposed update through `session.updated` and error handling; do not assume voice replacement is mutable because a general blog says so.
- Arabic input recognition, Arabic output voices, and supported end-to-end Arabic intake are separate capabilities. The public pages do not establish equivalent quality for this project.
- The source proves buffering, configuration, and control-flow properties. It does not quantify actual current latency, recognition error rates, or the frequency of interruption races. Measure those before claiming improvement percentages.
- The migration log says Twilio was unconfigured in recorded acceptance, and later notes still leave Twilio audio unverified. This research did not inspect live credentials or infer that today's account configuration is unchanged.
- Real UAE authorization rules, SLAs, procedure codes, identity integrations, and data-residency decisions cannot be inferred from the fictional catalogue. The research recommends the integration work; it does not certify those domain facts.

The highest-value pilot is a verified caller completing accurate intake, supplying the right documents, and reaching a human reviewer with enough evidence to act efficiently. That gives the project a measurable outcome beyond a convincing voice demonstration.
