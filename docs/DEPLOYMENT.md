# Deployment and phone numbers (zero budget)

> Looking for the free option? You do not have to deploy anything. [Local mode](LOCAL_MODE.md) runs the whole
> system — speech in, speech out, agent, rules, review — on your own machine, with no account, no public URL
> and no telephony provider. This document is for putting the backend where the *hosted* agent can reach it.

The ElevenLabs agent must reach this backend over public HTTPS. The quickest way is the one-command hosted mode
below. The manual options follow it, and after them an honest account of phone numbers.

## Hosted mode, one command

```bash
cp .env.example .env        # once; fill in HOSTED MODE — required, plus ONE tunnel provider
./scripts/run_hosted.sh     # every time
```

### Choosing a tunnel

The ElevenLabs agent needs a public https:// address for this backend that stays the same between runs. Two
providers are supported, and steps c to g do not care which one you use: they only see the resulting URL.

| | **ngrok (recommended)** | Cloudflare named tunnel |
|---|---|---|
| Cost | Free | Free tunnel, but **needs a domain you own** added to Cloudflare |
| Stable URL | Your free static domain, e.g. `sawt-al-tameen.ngrok-free.app` | A hostname on your domain |
| One-time setup | Sign up, install `ngrok`, copy the authtoken, claim the static domain | Add a domain, create a tunnel, publish a hostname on `localhost:8000`, install `cloudflared` |
| `.env` | `NGROK_AUTHTOKEN`, `NGROK_STATIC_DOMAIN` | `CLOUDFLARE_TUNNEL_TOKEN`, `PREAUTH_PUBLIC_BASE_URL` |
| Bad credentials caught | In step 0, before anything starts | In step b, after the backend has started |

`PREAUTH_TUNNEL_PROVIDER=ngrok` or `cloudflare` chooses explicitly. Left blank, the script uses whichever
provider has credentials filled in, preferring ngrok when both do, and stops with both options listed when
neither does.

**ngrok, one time:**

1. Sign up at [dashboard.ngrok.com/signup](https://dashboard.ngrok.com/signup) and install the agent from
   [ngrok.com/download](https://ngrok.com/download). You never start it yourself; the script does.
2. Copy your authtoken from
   [dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken) into
   `NGROK_AUTHTOKEN`.
3. Claim your free static domain at [dashboard.ngrok.com/domains](https://dashboard.ngrok.com/domains) and put the
   bare hostname (no `https://`) in `NGROK_STATIC_DOMAIN`.

Things to know about the free plan: one agent can be online at a time, and there is a monthly traffic allowance
that a demonstration will not approach. A browser opening the URL sees a one-time ngrok notice page first.
ElevenLabs' tool calls and webhooks are server-to-server and never see it.

**Cloudflare, one time:** the four steps at the end of [`.env.example`](../.env.example). The domain is the only
part that is not free. A named tunnel is needed rather than a quick one because a quick tunnel's URL changes
every run, which would break the ElevenLabs tools and webhook each time.

| Step | What happens | If it fails |
|---|---|---|
| 0 | Reads `.env`, checks every value is present, and makes one read-only ElevenLabs call to test the API key. With ngrok, it also connects briefly with your authtoken and static domain, then disconnects. Generates the voice-tool token and gateway secret on first run. | Lists every missing value, or says which credential was rejected (ElevenLabs key, ngrok authtoken or ngrok domain). Nothing is started. |
| a | Migrates the database, loads the catalogue if it is empty, and starts the backend on `PREAUTH_HOSTED_PORT`. | Prints the tail of `.hosted/backend.log`. |
| b | Starts the tunnel and waits until the public URL's `/health` answers. **ngrok:** `ngrok http <port> --url https://<NGROK_STATIC_DOMAIN>`. **Cloudflare:** `cloudflared tunnel run`. The token goes through the environment in both cases, never the command line. | ngrok: prints ngrok's error with the token redacted. Cloudflare: distinguishes a rejected token, a hostname routed to the wrong port (502) and a hostname that does not resolve. |
| c | Runs `scripts/verify_deployment.py` through the public URL and prints a PASS/FAIL banner. | Stops before touching ElevenLabs, and lists the failed checks. |
| d | Runs `scripts/elevenlabs_setup.py`: secret, three server tools, knowledge base, English-only agent prompt and keyterms. Updates in place on re-runs. | Prints ElevenLabs' error body. |
| e | Creates an HMAC workspace webhook for `/api/v1/voice/elevenlabs/post-call` (`POST /v1/workspace/webhooks`), stores the signing secret, and points post-call transcripts at it (`PATCH /v1/convai/settings`). Restarts the backend with the secret and re-verifies, including the webhook checks. | If your workspace already sends webhooks elsewhere, it asks before switching, because the setting is workspace-wide. If ElevenLabs returns no secret, it tells you where to copy it and waits. |
| f | Checks that `/api/v1/voice/twilio/inbound` is live and enforcing Twilio signatures (an unsigned probe must get 401), then prints the exact Twilio console setting. ElevenLabs' native number import is **not** used. | Never fails the run: if inbound calls are not configured it names the missing variables, and the browser test call still works. |
| g | Prints the backend URL, the browser test-call link, the phone number and anything still manual. Keeps running until Ctrl+C. | If the backend or tunnel dies later, it says which one and shows its log. |

A second run changes nothing in ElevenLabs if nothing changed. Webhook, tools, knowledge base and phone assignment
are all reused. Generated secrets live in `.hosted/secrets.env` (git-ignored, mode 600), never in `.env`, so local
mode is unaffected. `--yes` answers the workspace-webhook question in advance. `--no-tunnel` starts no tunnel,
for when `PREAUTH_PUBLIC_BASE_URL` already reaches this machine some other way.

Still configured in the dashboard, because no step above needs them for calls to work: the visual workflow with
per-node tool scoping, evaluation criteria and agent tests ([VOICE_AGENT.md](VOICE_AGENT.md)).

## Generate secrets first (manual options only)

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run three times
```

| Variable | Used by |
|---|---|
| `PREAUTH_VOICE_AGENT_TOKEN` | ElevenLabs tool calls (the setup script stores it as an ElevenLabs secret) |
| `PREAUTH_GATEWAY_SECRET` | You, when calling staff and reviewer APIs (`X-Gateway-Secret` header) |
| `PREAUTH_ELEVENLABS_WEBHOOK_SECRET` | Post-call webhook. Copy it from ElevenLabs after creating the webhook. |

Never commit these values. `.env` is git-ignored; see `.env.example`.

## Option A: your laptop plus a Cloudflare quick tunnel (free, no account, fastest)

Good for building and testing. Calls only work while your laptop and the tunnel are running, and the URL changes
every time the tunnel restarts.

```bash
# terminal 1: backend
uv sync --extra postgres
export PREAUTH_VOICE_AGENT_TOKEN=... PREAUTH_GATEWAY_SECRET=... PREAUTH_ELEVENLABS_WEBHOOK_SECRET=...
uv run alembic upgrade head
uv run python -m preauth.seed --if-empty      # loads the catalogue from knowledge_base/
uv run uvicorn preauth.main:app --port 8000

# terminal 2: public HTTPS URL
sudo pacman -S cloudflared          # Arch; other systems: github.com/cloudflare/cloudflared/releases
cloudflared tunnel --url http://localhost:8000
# prints https://<random>.trycloudflare.com

# terminal 3: point the agent at it
export ELEVENLABS_API_KEY=... PREAUTH_PUBLIC_BASE_URL=https://<random>.trycloudflare.com
uv run python scripts/elevenlabs_setup.py
```

When the tunnel URL changes, re-run the setup script and update the post-call webhook URL.

## Option B: always on, Hugging Face Spaces plus Neon Postgres (free tiers)

1. **Database.** Create a free project at [neon.tech](https://neon.tech) and copy its connection string
   (`postgresql://...sslmode=require`). The backend converts it to the right driver automatically.
2. **Space.** Create a Space at [huggingface.co/new-space](https://huggingface.co/new-space) with SDK **Docker** and
   visibility **Public**. ElevenLabs must reach it without a Hugging Face login.
3. **Code.** Push this repository to the Space, then copy `deploy/huggingface/README.md` over the Space's
   `README.md`. Hugging Face needs that file's front matter to build the Space as a Docker app on port 7860.
4. **Secrets.** In the Space settings, add `PREAUTH_DATABASE_URL`, `PREAUTH_VOICE_AGENT_TOKEN`,
   `PREAUTH_GATEWAY_SECRET` and `PREAUTH_ELEVENLABS_WEBHOOK_SECRET`. Optionally add `PREAUTH_SEED_SCENARIOS=1` to
   create the five demo cases on first start.
5. **URL.** The public URL is `https://<user>-<space>.hf.space`. Use it as `PREAUTH_PUBLIC_BASE_URL`.
6. **Space README.** Copy `deploy/huggingface/README.md` over the Space's `README.md` for the required front matter.

On start, the container runs migrations, loads synthetic reference data if the database is empty, and serves on
port 7860 (`scripts/start.sh`).

Caveats:
- Free Spaces go to sleep after a period without traffic. The first request after that is slow.
- Before a demo or test call, open `https://<your-space>.hf.space/health` and wait for `{"status":"ok"}`.
- Free-tier limits and sleep policies change. Check the current Hugging Face and Neon terms.

Render's free web services also work with the same Dockerfile. They sleep after about 15 minutes idle, though, and
the cold start can exceed the tools' 20-second timeout.

## Verify a deployment

Before pointing ElevenLabs at the backend, run the same sequence the agent will:

```bash
export PREAUTH_VOICE_AGENT_TOKEN=... PREAUTH_GATEWAY_SECRET=... PREAUTH_ELEVENLABS_WEBHOOK_SECRET=...
uv run python scripts/verify_deployment.py --base-url https://your-backend.example
```

Twenty-eight checks: caller verification (including a lapsed member and a wrong date of birth), supplier
onboarding, the rules and their cited sources, escalation with a cited ESC rule, the guardrails (no decision tool;
coverage blocked without verification; sign-off blocked until the transcript is logged), and the signed post-call
webhook. It closes the case it creates, and exits non-zero if any check fails.

## Phone numbers: what is and isn't free

These are checked facts as of September 2026. Verify them before you rely on them.

### Free today

- **Talk to the agent from your phone's browser.** The ElevenLabs test link
  (`https://elevenlabs.io/app/talk-to?agent_id=...`) runs the exact same agent, tools and backend over the internet.
  There are no call charges, and it works from India.
- **ElevenLabs Free plan:** 15 agent minutes per month and 4 concurrent calls, with no commercial licence. That is
  enough for a handful of test calls, but not for sustained demos. LLM usage is billed separately on paid plans.

### A real phone number (your own Twilio number, via register-call)

This deployment keeps the number in **your** Twilio account and does not import it into ElevenLabs. Twilio sends
each incoming call to the backend. The backend checks Twilio's signature, then registers the call with the agent
(`POST https://api.elevenlabs.io/v1/convai/twilio/register-call`), and passes back the TwiML that ElevenLabs
returns. From there it is an ordinary ElevenLabs conversation, using the same three tools and the same post-call
webhook.

**Twilio configuration (exact):**

> Twilio Console → **Phone Numbers** → **Manage** → **Active numbers** → *your number* → **Voice Configuration** →
> **A call comes in**: **Webhook** · URL `https://<your public URL>/api/v1/voice/twilio/inbound` · HTTP **POST** →
> **Save configuration**

With ngrok the public URL is `https://<NGROK_STATIC_DOMAIN>`; with Cloudflare it is `PREAUTH_PUBLIC_BASE_URL`.
`run_hosted.sh` prints the full URL at the end of every run.

**Backend configuration.** The endpoint stays disabled (HTTP 503) until all four of these are set, and it always
checks the `X-Twilio-Signature` header:

| Variable | Why |
|---|---|
| `TWILIO_AUTH_TOKEN` | Verifies each request comes from Twilio (HMAC-SHA1, Twilio's documented algorithm) |
| `PREAUTH_PUBLIC_BASE_URL` | Twilio signs the public URL it called, not the tunnel's local address, so this is what the signature is checked against |
| `ELEVENLABS_API_KEY` | Authenticates the register-call request |
| `PREAUTH_ELEVENLABS_AGENT_ID` | The agent the call is registered with. `run_hosted.sh` fills it in from `.elevenlabs-state.json` |

**Audio format.** Register-call requires the agent to use **μ-law 8000 Hz** for both input and output.
`scripts/elevenlabs_setup.py` sets this through the API (`conversation_config.asr.user_input_audio_format` and
`conversation_config.tts.agent_output_audio_format` = `ulaw_8000`), so `run_hosted.sh` applies it automatically.
In the dashboard, the same settings are under Agent → Voice → *TTS output format* and Agent → Advanced → *User
input audio format*, both set to "μ-law 8000 Hz". The browser test call still works at this format, at telephone
quality. `--audio-format pcm_16000` reverts it, for an agent that will never take phone calls.

**Behaviour.**
- If a request is missing `From` or `To`, it gets `400 TWILIO_CALL_INVALID`.
- A missing or wrong signature gets `401 TWILIO_SIGNATURE_INVALID`.
- If ElevenLabs refuses or cannot be reached, the endpoint still returns `200 application/xml` with TwiML that
  apologises and hangs up. The caller never hears Twilio's generic "application error", and the failure is logged
  as `elevenlabs_register_call_failed` with the CallSid.
- Logs carry the CallSid and only the last four digits of each number, never tokens.

**Limits.**
- ElevenLabs cannot transfer a register-call call, because it has no access to your Twilio credentials.
- Trial Twilio accounts only accept calls from numbers you have verified in Twilio, and play a trial notice first.
  A US number costs roughly a dollar or two a month on a paid account, plus per-minute charges.
- Calling a US number from an Indian mobile is an international call, charged by your operator.

### A UAE (+971) number

- **Twilio.** Its UAE regulatory guidelines list only **toll-free (+971 800)** numbers. Calls to those must
  originate inside the UAE, so you cannot test one from India. They also need regulatory documents and are paid.
- **Local UAE numbers.** These are issued through UAE-licensed operators (usually via a business SIP provider with
  local presence). They require company KYC and are not free.
- **Any number you obtain.** ElevenLabs can attach it through **SIP trunk** import without code changes.
- **For the challenge.** The brief mentions live test numbers for Stage 2. Ask the Ignyte × ElevenLabs organisers
  whether they provide a UAE test number or telephony credits. That is the realistic zero-budget route to a +971
  number.

### Suggested path

1. Build and test using the browser link, from your laptop and your phone.
2. Record your ElevenLabs Tests pass rates (see [VOICE_AGENT.md](VOICE_AGENT.md)).
3. For a phone demo, either use the organisers' number or spend a few dollars on a Twilio US number; the agent is
   the same.

Sources: [Twilio UAE regulatory guidelines](https://www.twilio.com/en-us/guidelines/ae/regulatory),
[Twilio UAE number terms](https://www.twilio.com/en-us/legal/service-country-specific-terms/uae-phone-numbers),
[ElevenLabs Agents pricing](https://elevenlabs.io/pricing/agents),
[ElevenLabs register-call](https://elevenlabs.io/docs/api-reference/twilio/register-call),
[Register Twilio calls](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call),
[Twilio webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security),
[Connect Twilio to ElevenLabs](https://elevenlabs.io/agents/integrations/twilio).
