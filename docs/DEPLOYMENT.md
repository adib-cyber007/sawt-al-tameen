# Deployment and phone numbers

AssemblyAI is the default hosted voice provider. Hosted mode needs a stable public HTTPS URL so AssemblyAI can
deliver completed-session events, browsers can reach the voice WebSocket, and Twilio can reach the inbound/media
endpoints. Local mode remains credential-free; see [LOCAL_MODE.md](LOCAL_MODE.md).

## One-command hosted mode

```bash
cp .env.example .env
# Fill ASSEMBLYAI_API_KEY, PREAUTH_ASSEMBLYAI_VOICE_ID, and one tunnel provider.
./scripts/run_hosted.sh
```

The launcher performs these steps in order:

| Step | Action | Failure behavior |
|---|---|---|
| 0 | Validates `VOICE_PROVIDER`, required credentials, tunnel/Twilio values, and makes a read-only provider API call. Generates independent voice-tool, gateway, AssemblyAI webhook and media secrets in `.hosted/secrets.env`. | Stops before starting services and reports each missing/rejected value. |
| a | Migrates the database, seeds the catalogue if empty, and starts the backend on `PREAUTH_HOSTED_PORT`. | Prints the backend log tail. |
| b | Starts ngrok with a static domain or a Cloudflare named tunnel and waits for public `/health`. | Distinguishes credential, DNS, routing and wrong-port failures. |
| c | Runs `scripts/verify_deployment.py` through the public URL. | Stops before changing provider resources when any invariant fails. |
| d | Runs `scripts/assemblyai_setup.py`, creating/updating 24 kHz browser and 8 kHz PCMU phone agents plus their `session.completed` subscriptions. | Prints the bounded provider error without exposing credentials. |
| e | Restarts when generated agent ids changed and re-verifies. | Leaves the previous provider resources intact for a safe rerun. |
| f | Probes the Twilio inbound endpoint; unsigned requests must be rejected. Prints the exact Twilio console setting. | Phone configuration is optional; browser calling still works. |
| g | Prints `/voice`, the Twilio webhook, transcript endpoint, tunnel and log paths; keeps processes alive until Ctrl+C. | Reports if a child process exits. |

Provisioning is idempotent. Generated resource ids are stored in git-ignored `.assemblyai-state.json`, and secrets
in git-ignored `.hosted/secrets.env`. A rerun updates existing agents/subscriptions.

## Required configuration

| Variable | Secret | Purpose |
|---|---:|---|
| `VOICE_PROVIDER=assemblyai` | No | Selects the migrated hosted provider |
| `ASSEMBLYAI_API_KEY` | Yes | Voice Agent REST/WebSocket, Sessions and LLM Gateway |
| `PREAUTH_ASSEMBLYAI_VOICE_ID` | No | Selected English voice |
| `PREAUTH_PUBLIC_BASE_URL` | No | Stable HTTPS URL (derived from ngrok domain when ngrok is selected) |
| `PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET` | Yes | Generated HMAC secret for completed-session deliveries |
| `PREAUTH_ASSEMBLYAI_MEDIA_SECRET` | Yes | Generated signing key for short-lived Twilio media URLs |
| `PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID` | No | Generated browser-agent id |
| `PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID` | No | Generated phone-agent id |
| `PREAUTH_GATEWAY_SECRET` | Yes | Generated protection for staff/reviewer APIs |
| `PREAUTH_VOICE_AGENT_TOKEN` | Yes | Generated compatibility token for the HTTP tool transport/deployment checks |

Never commit real values. `ASSEMBLYAI_API_KEY` is used only server-side.

## Tunnels

Choose one option in `.env`.

### ngrok static domain

Set `NGROK_AUTHTOKEN` and the bare `NGROK_STATIC_DOMAIN`. The launcher derives
`PREAUTH_PUBLIC_BASE_URL=https://<domain>`, validates that the token/domain pair can register, and then starts the
tunnel against `PREAUTH_HOSTED_PORT`.

### Cloudflare named tunnel

Set `CLOUDFLARE_TUNNEL_TOKEN` and `PREAUTH_PUBLIC_BASE_URL`. The tunnel's published application route must point
to `http://localhost:<PREAUTH_HOSTED_PORT>`. A changing quick-tunnel URL is unsuitable because it invalidates
webhook, browser and Twilio routing on every restart.

### Existing public endpoint

Run `./scripts/run_hosted.sh --no-tunnel` with `PREAUTH_PUBLIC_BASE_URL` when another HTTPS reverse proxy already
reaches this machine.

## Manual provider setup

To inspect provider payloads without network writes:

```bash
python scripts/assemblyai_setup.py --dry-run
```

For a manual live run, export `ASSEMBLYAI_API_KEY`, `PREAUTH_ASSEMBLYAI_VOICE_ID`,
`PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET`, and `PREAUTH_PUBLIC_BASE_URL`, then run:

```bash
python scripts/assemblyai_setup.py
```

Put the printed browser and phone ids into the corresponding environment variables before starting the backend.

## Browser calling

Open `https://<public-base-url>/voice`. The page connects only to the backend. The AssemblyAI API key stays on the
server. Browser audio is mono signed PCM16 at 24 kHz in both directions.

## Twilio phone calling

Set:

```dotenv
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+14155550123   # optional display value
```

In Twilio Console:

> Phone Numbers → Manage → Active numbers → your number → Voice Configuration → A call comes in → Webhook →
> `https://<public-base-url>/api/v1/voice/twilio/inbound` → HTTP POST → Save

The backend verifies the Twilio request signature, issues a 90-second token bound to `CallSid`, and returns TwiML
that opens `/api/v1/voice/assemblyai/twilio`. The media socket verifies the token and Twilio start identity, then
passes native 8 kHz PCMU to the stored phone agent. No Twilio credentials are sent to AssemblyAI.

An unsigned inbound request returns 401 when configuration is complete. A 503 response names missing variables.

## Deployment verification

Against a running deployment:

```bash
python scripts/verify_deployment.py --base-url https://your-backend.example
```

The verifier exercises authentication, active/lapsed identity checks, supplier routing, document/rule evaluation,
escalation citations, absence of decision tools, transcript-before-sign-off, and the AssemblyAI webhook signature
boundary. It cannot fabricate an authoritative AssemblyAI session; live transcript ingestion is accepted with a
real call and is covered offline by the fake-provider integration suite.

## Live acceptance checklist

Before removing the rollback path, record in `MIGRATION_LOG.md`:

1. browser and Twilio calls with representative English audio;
2. policy/provider/procedure identifier accuracy;
3. first-transcript, first-audio, turn and tool latency against the baseline;
4. silence, noise, barge-in, disconnect and concurrent-call behavior;
5. valid completed-session ingestion, delayed-artifact retry and reconciliation;
6. reviewer sign-off blocked before and allowed after the matching transcript;
7. selected voice-persona stakeholder approval.

## Reconciliation and operations

If a completed-session webhook is missed:

```bash
python scripts/assemblyai_reconcile.py
```

The command lists completed sessions and uses the same idempotent artifact-ingestion path as the webhook. Existing
records are left unchanged; artifacts still being prepared remain pending.

Logs are in `.hosted/backend.log` and `.hosted/tunnel.log`. API health is `/health`; OpenAPI is `/docs`.

## Rollback during acceptance

Set `VOICE_PROVIDER=elevenlabs`, restore `ELEVENLABS_API_KEY`, `PREAUTH_ELEVENLABS_AGENT_ID`,
`PREAUTH_ELEVENLABS_WEBHOOK_SECRET`, and restart. If the Twilio URL was changed, restore the same application
inbound endpoint—it dispatches by provider. The retained ElevenLabs setup, register-call client, webhook route,
tests and `.elevenlabs-state.json` make rollback immediate. Do not delete either provider's remote resources as
part of rollback.
