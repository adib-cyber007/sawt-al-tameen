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
| `ASSEMBLYAI_API_KEY` | Yes | Voice Agent REST/WebSocket and Sessions API |
| `PREAUTH_ASSEMBLYAI_VOICE_ID` | No | English voice; defaults to `ivy` (professional and deliberate) in `.env.example` |
| `PREAUTH_PUBLIC_BASE_URL` | No | Stable HTTPS URL (derived from ngrok domain when ngrok is selected) |
| `PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET` | Yes | Generated HMAC secret for completed-session deliveries |
| `PREAUTH_ASSEMBLYAI_MEDIA_SECRET` | Yes | Generated signing key for short-lived Twilio media URLs |
| `PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID` | No | Generated browser-agent id |
| `PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID` | No | Generated phone-agent id |
| `PREAUTH_GATEWAY_SECRET` | Yes | Generated protection for staff/reviewer APIs |
| `PREAUTH_VOICE_TOOL_TOKEN` | Yes | Generated token for the provider-neutral deployment diagnostic endpoint |

Never commit real values. `ASSEMBLYAI_API_KEY` is used only server-side.

## Tunnels

Choose one option in `.env`.

### ngrok static domain

Set `NGROK_AUTHTOKEN` and the bare `NGROK_STATIC_DOMAIN`. The launcher derives
`PREAUTH_PUBLIC_BASE_URL=https://<domain>`, validates that the token/domain pair can register, and then starts the
tunnel against `PREAUTH_HOSTED_PORT`. The hostname stays the same across runs, but it only reaches the app while
the backend and ngrok processes are running on this computer. Sleeping or shutting down the computer makes it
unavailable.

On Windows, run `powershell -File scripts/start_hosted.ps1` from the repository root to start both processes in the
background. It waits for the public health check and writes startup output to `.hosted/launcher.stdout.log` and
`.hosted/launcher.stderr.log`. Repeating it while the site is healthy does not start a second copy. This command
does not configure automatic startup after a reboot. To restore the service automatically when you sign in, run
`powershell -File scripts/install_hosted_autostart.ps1` once. It registers a current-user Windows task and retries
startup if the network is not ready. It does not keep the site online while the computer is asleep or signed out.

On Windows, the running hosted launcher asks the system to stay awake while the laptop is plugged into AC power.
It releases that request when unplugged or stopped. Battery sleep settings are unchanged. Closing the lid,
pressing the power button, hibernating, or switching off the computer can still take the ngrok endpoint offline.
Keep the laptop plugged in and awake for continuous testing from its static URL.

The first visit to a free ngrok domain in a browser may display ngrok's warning page. Use its Visit Site button
once before testing `/voice`. The startup health check bypasses this HTML page so it checks the app itself.

If ngrok reports `ERR_NGROK_3004`, check whether `http://127.0.0.1:<PREAUTH_HOSTED_PORT>/health` responds and
read `.hosted/tunnel.log` at the time of failure. The launcher forwards to an explicit HTTP loopback URL and,
while running, checks the public `/health` every 15 seconds. After three consecutive public failures while the
local backend is healthy, it restarts ngrok on the same static domain. A dropped internet connection can still
interrupt a current call. For uninterrupted availability while this computer sleeps or loses connectivity,
deploy the Docker image and a persistent PostgreSQL database to an always-on host.

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
server. The browser captures at the device's native rate and resamples to mono signed PCM16 at 24 kHz; provider
audio is mono signed PCM16 at 24 kHz in the other direction.

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
that opens `/api/v1/voice/assemblyai/twilio` with the token in a nested custom parameter. The media socket verifies
the upgrade signature, bounds the start handshake, and checks the token and Twilio identities. A durable admission
claim rejects replay before opening the stored phone agent. It then passes native 8 kHz PCMU audio.
Apply `uv run alembic upgrade head` (migration `0002`) before starting this version.

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


See [voice reliability](VOICE_RELIABILITY.md) for the Twilio custom-parameter handshake,
non-blocking tool dispatch, durable retries, and migration `0002` rollout requirements.
