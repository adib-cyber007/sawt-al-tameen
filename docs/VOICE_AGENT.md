# Voice agent (AssemblyAI)

AssemblyAI is the default hosted voice provider. The application owns both browser and Twilio media transports,
keeps the provider credential on the server, and exposes the same three safety-constrained business tools used by
local mode. Automated voice service is English-only.

```mermaid
flowchart LR
    Browser[Browser microphone\nPCM16 mono 24 kHz] --> BWS[Backend WebSocket\n/api/v1/voice/assemblyai/browser]
    Twilio[Twilio call\nPCMU mono 8 kHz] --> TWS[Signed Media Stream\n/api/v1/voice/assemblyai/twilio]
    BWS --> AAI[AssemblyAI stored Voice Agent]
    TWS --> AAI
    AAI --> Bridge[Realtime bridge\nsession + barge-in + tool correlation]
    Bridge --> Tools[verify_caller\ncheck_coverage_rule\nlog_transcript]
    Tools --> Rules[Rules · recommendations · audit]
    AAI --> Hook[Signed session.completed webhook]
    Hook --> Timeline[Sessions API timeline artifact]
    Timeline --> Record[Immutable call record]
    Record --> Review[Human review can proceed]
```

## What is provisioned

`scripts/assemblyai_setup.py` idempotently creates two stored agents from the same source-controlled English
prompt and audio configuration:

| Agent | Input/output | Why separate |
|---|---|---|
| Browser | signed PCM16 mono, 24 kHz | Native browser capture/playback without telephony degradation |
| Phone | G.711 PCMU mono, 8 kHz | Native Twilio Media Streams format; no application transcoding |

Both stored agents use:

- `voice/system_prompt.md`, which forbids final decisions and non-English automated intake;
- AssemblyAI's managed Voice Agent conversational model (`llm: []`), so no separate LLM Gateway entitlement is
  required;
- an English language constraint, UAE health-insurance transcription context, caller voice isolation, and
  source-derived keyterms for policy ids, procedure codes, providers and UAE terminology;
- a `session.completed` subscription pointing at `/api/v1/voice/assemblyai/post-call`.

Stored agents deliberately contain no HTTP tools. After the provider returns `session.ready`, the backend bridge
attaches exactly `verify_caller`, `check_coverage_rule`, and `log_transcript` as client-side function tools and
waits for `session.updated` before forwarding caller audio. This satisfies AssemblyAI's initial-update rule
(`agent_id` is mutually exclusive with inline configuration) while preserving trusted local tool execution and
provider-session correlation.

The generated ids and subscription ids are in git-ignored `.assemblyai-state.json`. Re-running updates the same
resources rather than creating duplicates.

## Configure and provision

Set these values in `.env`:

```dotenv
VOICE_PROVIDER=assemblyai
ASSEMBLYAI_API_KEY=...
PREAUTH_ASSEMBLYAI_VOICE_ID=...
```

`scripts/run_hosted.sh` generates independent webhook/media secrets, provisions both agents, restarts the backend
with their ids, and prints the browser and Twilio URLs. To inspect payloads without network writes:

```bash
python scripts/assemblyai_setup.py --dry-run
```

To provision manually, also set `PREAUTH_PUBLIC_BASE_URL` and
`PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET`, then run:

```bash
python scripts/assemblyai_setup.py
```

The REST API uses AssemblyAI's raw API-key `Authorization` value. The realtime WebSocket uses
`Authorization: Bearer <key>`. This difference is intentional and covered by tests. The API key never appears in
browser JavaScript, TwiML, logs, or stored call metadata.

## Browser calls

With hosted mode running, open:

```text
https://<public-base-url>/voice
```

The page captures mono audio at the device's native rate, keeps browser acoustic echo cancellation enabled,
resamples it to signed PCM16 at 24 kHz in an AudioWorklet, and streams it through the backend. A separate playback
AudioWorklet resamples the provider's 24 kHz PCM packets to the device rate. Playback starts when three seconds
of speech are available, when a shorter reply completes, or after five seconds of buffering with available audio.
This reserve absorbs the two-second delivery gaps measured in live greetings without waiting for the entire
reply. The five-second limit starts when the reply reaches the front of the playback queue; it cannot bound
provider generation time before the first audio packet. Delivery slower than playback can still cause a stall.
The page displays **Preparing the reply** during buffering. Separate reply queues preserve reply boundaries,
including when a later reply arrives before the previous one finishes playing. Natural speech pauses are retained.
Browser calls use speaker-safe turn taking: a separate playback control output gates microphone capture
on the audio render thread throughout each reply, including network gaps, until the final queued sample plays.
Capture then sends silence for another 350 ms plus reported device output latency to cover residual speaker
echo. Browser echo cancellation and noise suppression are both requested. Wait for the displayed **Listening**
status before speaking; voice interruption during an assistant reply is disabled in both the browser and the
provider configuration for this mode. The browser requests playback-oriented hardware latency to reduce
Brave/Chromium audio glitches under load.
The local recording console pauses assistant playback before recording and prevents playback during recording.
Microphone permission failures, missing devices and devices in use show actionable errors. Muted or disconnected
tracks no longer appear as listening. If the browser interrupts audio, use **Resume audio**; if the microphone
disconnects, reconnect it and start a new call. **End call** also cancels startup while permission is pending.
Use the HTTPS site address when testing from a phone; a plain HTTP LAN address cannot access the microphone.
The page also displays transcript events. Browser
noise suppression is disabled because AssemblyAI Voice Focus already performs that job; stacking both degrades
recognition. The backend starts the stored browser agent only after the browser WebSocket is accepted.

## Twilio calls

Point the Twilio number's incoming-call webhook to:

```text
POST https://<public-base-url>/api/v1/voice/twilio/inbound
```

The endpoint verifies `X-Twilio-Signature` and returns TwiML containing a 90-second media token bound to the
Twilio `CallSid`. The media WebSocket verifies the token and the `start.callSid` before forwarding audio. PCMU is
passed through at 8 kHz; on caller interruption the bridge clears Twilio's queued output.

Required phone configuration:

```dotenv
TWILIO_AUTH_TOKEN=...
PREAUTH_PUBLIC_BASE_URL=https://...
PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID=...     # populated by hosted setup
PREAUTH_ASSEMBLYAI_MEDIA_SECRET=...       # generated by hosted setup
```

## Tools and session correlation

AssemblyAI function calls execute in the backend through the existing `VoiceToolGateway` with the authoritative
AssemblyAI `session_id`. Calls run in order without blocking the provider audio receive loop. Tool results are
sent only after the associated reply completes; interrupted replies do
not leak stale results into a later turn. No function exists that can approve, deny, or finalise a case.

That same `session_id` is written to every tool invocation. It later becomes the call-record conversation id, so
a reviewer sees `CALL_RECORD_PENDING` until the exact session transcript is durable.

If the upstream WebSocket drops, or AssemblyAI reports a retryable `server_error`, `agent_init_failed`, or
`agent_timeout`, the bridge reconnects with `session.resume` and the same session id. Recovery is limited to three
attempts inside AssemblyAI's 30-second preservation window. Browser or Twilio termination, call-identity failures,
terminal provider errors, and refused/expired resumes are not converted into a fresh session: the bridge closes
the client visibly instead of leaving a silent call or losing conversation context and transcript correlation.

## Post-call transcript and reconciliation

The signed webhook is a notification, not the transcript. The application:

1. verifies `X-AAI-Signature` against the raw body with a five-minute replay window;
2. fetches the authoritative session from the Voice Agent Sessions API;
3. returns retryable `503 WEBHOOK_ARTIFACT_PENDING` while the timeline artifact is unavailable;
4. downloads the pre-signed timeline without attaching the API key;
5. normalizes caller, agent and tool turns plus latency/confidence/interruption metrics;
6. stores one immutable call record and links it to every case touched by that session.

Deliveries are idempotent by session id. Pre-signed artifact URLs are never persisted. If delivery was exhausted
or missed, run:

```bash
python scripts/assemblyai_reconcile.py
python scripts/assemblyai_reconcile.py --agent-id <agent-id> --limit 500
```

## English-only behavior

The automated line always responds in English. If a caller cannot continue in English, it takes a name and
callback number, logs `OUT_OF_SCOPE`, and routes the request to a person. It does not translate or continue the
pre-authorisation flow. `voice/agent_tests.json` includes this behavior as a provider-dashboard acceptance case.

## Verification and live acceptance

Offline tests cover audio formats, realtime events, tool correlation, barge-in, signed media tokens, webhook
authentication/replay protection, artifact delay, provider errors, duplicate delivery, reconciliation semantics,
bounded browser/Twilio session resumption, and the transcript-before-sign-off invariant.

If the greeting plays but every later caller turn has no agent transcript or audio, retrieve the session timeline
and inspect the stored agent's `llm` field first. `scripts/assemblyai_setup.py` deliberately sends `"llm": []` on
every update; this removes stale custom-model settings that can accept the greeting but fail every generated reply.

The 2026-09-20 hosted acceptance used a 24 kHz English utterance containing a caller name and hospital name. The
public WebSocket transcribed it exactly and returned both an appropriate agent transcript and spoken audio. Keep
using representative human speech to accept:

- selected voice quality and pronunciation of policy/procedure identifiers;
- browser and Twilio first-transcript/first-audio latency;
- interruption, silence, background noise and live disconnect/resume behavior;
- concurrent-call limits, rate limiting, webhook delay and reconciliation;
- transcription accuracy versus the pre-migration baseline.

Record measured results in `MIGRATION_LOG.md` as deployment acceptance evidence.

For a repeatable transport/audio smoke test, provide a mono PCM16 24 kHz WAV:

```bash
node scripts/voice_audio_smoke.mjs \
  wss://<public-base-url>/api/v1/voice/assemblyai/browser sample.wav 45000
```
