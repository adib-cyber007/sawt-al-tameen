<div align="center">

<img src="docs/assets/sawt-al-tameen-art.png" alt="Sawt al-Tameen, a voice agent for health-insurance pre-authorisation" width="100%">

<h1>Sawt al-Tameen</h1>

<h3>𝐒 𝐀 𝐖 𝐓  𝐀 𝐋  𝐓 𝐀 𝐌 𝐄 𝐄 𝐍</h3>

𝘛𝘩𝘦 𝘷𝘰𝘪𝘤𝘦 𝘰𝘧 𝘪𝘯𝘴𝘶𝘳𝘢𝘯𝘤𝘦

An English-language voice line that takes pre-authorisation calls for a UAE health insurer,<br>
checks every request against the benefit schedule, and leaves the decision to a person.

English only ⋄ AED throughout ⋄ Dubai, Abu Dhabi and Sharjah ⋄ DHA and DOH structure

**AssemblyAI Voice Agent Hackathon, September 2026**
[Try the live voice agent](https://dividable-fretted-aroma.ngrok-free.dev/voice) ·
[Judge demo guide](https://dividable-fretted-aroma.ngrok-free.dev/voice/assets/judges.html) ·
[Submission package](docs/submission/SUBMISSION.md) · [MIT license](LICENSE)

The browser demo needs no login and uses fictional records. Hosting runs on a Windows computer through a static
ngrok tunnel, so that computer must stay online. Document uploads require an operator key. The Twilio bridge is
implemented, but a live phone number is not configured. See the [acceptance report](docs/CONVERSATION_ACCEPTANCE.md)
for measured workflows and remaining conversation limits.

[The rule](#rule) · [How a call flows](#flow) · [Tools](#tools) · [Catalogue](#catalogue) · [Run it](#run) ·
[Phone calls](#phone) · [Testing](#testing) · [Layout](#layout) · [Docs](#docs)

</div>

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="rule"></a>

## 1 ⋄ 𝐓𝐡𝐞 𝐫𝐮𝐥𝐞 𝐭𝐡𝐚𝐭 𝐬𝐡𝐚𝐩𝐞𝐬 𝐞𝐯𝐞𝐫𝐲𝐭𝐡𝐢𝐧𝐠

An automated line must never tell a clinic that treatment is approved or denied. Here that is not an instruction
the model could be talked out of. It is how the software is built:

- The agent has **three tools**, and none of them can approve, deny or finalise anything.
- Recording a decision is a **reviewer-only API**, and the voice agent has no credentials for it.
- The case state machine lets only a `HUMAN_REVIEWER` move a case to `APPROVED` or `DENIED`, and refuses to load
  if that table is ever edited otherwise.
- A case a call has touched **cannot be signed off until the call transcript is on record**.
- A human decision never overwrites the system's recommendation. Both are kept, append-only.

Ask the agent to approve something and it declines, every time, because there is nothing there for it to call.

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="flow"></a>

## 2 ⋄ 𝐇𝐨𝐰 𝐚 𝐜𝐚𝐥𝐥 𝐟𝐥𝐨𝐰𝐬

```
      phone call                 browser                   browser or terminal
          │                         │                               │
    your Twilio number          /voice UI                     local agent
          │                         │                   Whisper · Ollama · Piper
 /api/v1/voice/twilio/inbound       │                               │
          │               backend realtime bridges                 │
          └──────────────▶ AssemblyAI Voice Agents                  │
                         PCMU phone · PCM browser                   │
                                    │                               │
                                    └───────────────┬───────────────┘
                                                    │
                          verify_caller · check_coverage_rule · log_transcript
                                                    │
                                                    ▼
                               rules engine · recommendation · audit trail
                                                    │
                                                    ▼
                         review queue ──▶ qualified human ──▶ APPROVED or DENIED
```

| | What happens |
|---|---|
| **Identify** | Clinic, broker or supplier? Suppliers and patients are routed away from the pre-authorisation flow. |
| **Verify** | Provider number, then policy number and date of birth. A lapsed policy stops here. |
| **Collect** | Procedure code, estimated cost in AED and treatment date, each read back digit by digit. |
| **Check** | Tier limits, co-payments, network nesting, waiting periods, documents and thresholds. |
| **Answer** | A prepared recommendation, a list of missing documents, or an escalation that cites the exact rule. |
| **Log** | The call is recorded and the references are read back. From here, a human takes over. |

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="tools"></a>

## 3 ⋄ 𝐓𝐡𝐫𝐞𝐞 𝐭𝐨𝐨𝐥𝐬, 𝐚𝐧𝐝 𝐧𝐨𝐭𝐡𝐢𝐧𝐠 𝐞𝐥𝐬𝐞

| Tool | Purpose |
|---|---|
| `verify_caller` | Identifies the organisation and the member; returns the tier and dependants, or why verification failed |
| `check_coverage_rule` | Checks a complete request; prepares a recommendation or escalates, citing the rule |
| `log_transcript` | Records what the caller was told, and raises a callback when a human must follow up |

`check_coverage_rule` refuses to run without a verification from `verify_caller`, so no caller gets a coverage
answer before they have been identified.

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="catalogue"></a>

## 4 ⋄ 𝐓𝐡𝐞 𝐛𝐞𝐧𝐞𝐟𝐢𝐭 𝐜𝐚𝐭𝐚𝐥𝐨𝐠𝐮𝐞

[`knowledge_base/`](knowledge_base/README.md) is the single source of truth. The rules engine decides from it and
the agent retrieves the same files. When the agent cites *"Section 4.14 of the Executive Schedule of Benefits"*,
that section really exists in a document the agent can quote.

| Tier | Annual limit | Network | Pre-auth threshold | Outpatient co-pay |
|---|---|---|---|---|
| Basic | AED 150,000 | Basic Network | AED 1,000 | 20% |
| Enhanced | AED 500,000 | Enhanced Network | AED 2,500 | 20% |
| Comprehensive | AED 1,000,000 | Comprehensive Network | AED 5,000 | 10% |
| Executive | AED 3,000,000 | Executive Network (worldwide ex-USA) | AED 10,000 | 0% |

Networks nest, Basic ⊂ Enhanced ⊂ Comprehensive ⊂ Executive, and only Executive covers out-of-network care. The
catalogue also holds **50 procedures** (36 clear, 11 ambiguous, 3 excluded), **20 providers** across three
emirates and **20 members** (three of them lapsed). Where the rules stop, one of **eight escalation rules**
hands the case to a person, and the escalation quotes that rule's own words:

| | Hands the case to a person when | | Hands the case to a person when |
|---|---|---|---|
| `ESC-001` | required documents are missing | `ESC-005` | the procedure is unscheduled or new technology |
| `ESC-002` | two policy clauses conflict | `ESC-006` | clinical and cosmetic intent are unclear |
| `ESC-003` | a limit or sub-limit is exceeded | `ESC-007` | eligibility is in doubt |
| `ESC-004` | diagnosis or procedure coding is disputed | `ESC-008` | the provider is suspended, onboarding or out of network |

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="run"></a>

## 5 ⋄ 𝐑𝐮𝐧 𝐢𝐭

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is optional; it is only needed for PostgreSQL.

**The backend on its own:**

```bash
uv sync --extra postgres
uv run alembic upgrade head                  # create the schema
uv run python -m preauth.seed --scenarios    # load the catalogue and five demo cases
uv run uvicorn preauth.main:app --reload     # http://localhost:8000/docs
uv run python scripts/simulate_conversations.py   # five complete calls, with transcripts and assertions
```

**Fully local: free and offline.** The LLM runs on Ollama, speech-to-text on faster-whisper and text-to-speech on
Piper. The same three tools, rules and review queue sit underneath.

```bash
uv sync --extra local --extra local-voice
ollama pull qwen2.5-coder:7b
uv run python -m piper.download_voices en_GB-alba-medium --data-dir ./models/piper
./scripts/check_local.sh      # names anything missing, with the command that fixes it
./scripts/run_local.sh        # takes the first free port from 8000; open /local in a browser
uv run python -m preauth.local_cli   # the same agent in a terminal, no audio needed
```

**Hosted, with AssemblyAI.** Copy `.env.example` to `.env`, set the API key, English voice id and one tunnel
provider, then run one command:

```bash
./scripts/run_hosted.sh
```

On Windows, `powershell -File scripts/start_hosted.ps1` starts the same hosted service in the background and
checks the static URL before returning. The computer must stay awake; see [hosted deployment](docs/DEPLOYMENT.md#ngrok-static-domain).

It starts the backend and a stable HTTPS tunnel, runs deployment checks, creates or updates separate 24 kHz browser
and 8 kHz PCMU phone agents, registers signed completed-session webhooks, and prints the `/voice` test page. A
second run updates the same resources. Browser capture is resampled from the device rate to 24 kHz for consistent
Chrome, Firefox and Safari recognition. AssemblyAI is the sole hosted voice provider.

Guides: [local mode](docs/LOCAL_MODE.md) · [hosted deployment](docs/DEPLOYMENT.md#one-command-hosted-mode)

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="phone"></a>

## 6 ⋄ 𝐏𝐡𝐨𝐧𝐞 𝐜𝐚𝐥𝐥𝐬

Calls come in on **your own Twilio number**. Twilio posts each call to the backend, which verifies Twilio's
signature and returns TwiML for a short-lived, CallSid-bound Media Stream. The backend bridges native 8 kHz PCMU
audio to the AssemblyAI phone agent. The one Twilio setting:

> Phone Numbers → Active numbers → *your number* → Voice Configuration → **A call comes in**: Webhook,
> `https://<public URL>/api/v1/voice/twilio/inbound`, HTTP **POST**

The endpoint stays switched off until its Twilio, AssemblyAI agent and media-signing configuration is complete,
and it never accepts an unsigned request. Details: [DEPLOYMENT.md](docs/DEPLOYMENT.md#twilio-phone-calling).

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="testing"></a>

## 7 ⋄ 𝐓𝐞𝐬𝐭𝐢𝐧𝐠

```bash
uv run pytest                                             # SQLite
node --test tests/voice_audio.test.mjs                     # audio conversion and call cleanup (Node 24)
docker compose up -d --wait                               # PostgreSQL
PREAUTH_TEST_DATABASE_URL=postgres://preauth:preauth@localhost:55432/preauth uv run pytest
```

**393 Python tests** and **46 JavaScript audio tests** pass locally (30 September 2026); CI runs the Python suite against SQLite and PostgreSQL. They build the
schema through the real Alembic migration, so the migration itself is tested. Among other things, they pin down that:
- all three lapsed members are rejected;
- all eleven ambiguous procedures escalate, citing their own rule;
- missing information is never reported as a failure;
- no rule result can be changed after the fact;
- a forged Twilio signature is refused;
- no agent, hosted or local, can reach a tool that decides anything.

No test needs a model download or a network connection.

| Command | What it proves |
|---|---|
| `scripts/verify_deployment.py` | A live deployment behaves correctly end to end (26 checks) |
| `scripts/simulate_conversations.py` | Five call shapes, including a caller demanding a decision (30 checks) |
| `scripts/generate_uae_knowledge_base.py --check` | The catalogue is internally consistent |
| `scripts/voice_audio_smoke.mjs` | A 24 kHz PCM WAV is transcribed and receives agent text plus audio over the live browser WebSocket |
| `scripts/check_local.sh` | What local mode still needs on this machine |

After changing routes, schemas or the catalogue, regenerate the derived files:
`uv run python scripts/export_api_docs.py` and `uv run python scripts/generate_uae_knowledge_base.py`.

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="layout"></a>

## 8 ⋄ 𝐑𝐞𝐩𝐨𝐬𝐢𝐭𝐨𝐫𝐲 𝐥𝐚𝐲𝐨𝐮𝐭

```
knowledge_base/          the benefit catalogue — schedules, procedures, providers, members, escalation rules
voice/                   agent prompts (hosted and local) and dashboard test definitions
src/preauth/
  domain/                case state machine, review policy, errors — no I/O
  rules/                 the ruleset and its engine (pure)
  recommendation/        rule results → recommendation (pure)
  application/           desk, evaluation, review, callbacks, audit, Twilio inbound
  infrastructure/        ORM, migration, repositories, logging, signature checks
  api/                   HTTP routes, schemas, error envelope
  agent_tools/           the three tools and hosted-provider schema adapters
  local/                 the local channel and its browser console
scripts/                 run, set up, verify, simulate, generate
docs/                    architecture, deployment, voice agent, local mode, API
```

<a id="docs"></a>

| Document | Contents |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Layers, the state machine, how decision authority is enforced, known limitations |
| [Deployment](docs/DEPLOYMENT.md) | One-command hosting, tunnels, Twilio, and an honest account of UAE phone numbers |
| [Voice agent](docs/VOICE_AGENT.md) | Tools, setup script, workflow nodes, evaluation criteria, terminology |
| [Local mode](docs/LOCAL_MODE.md) | Running free and offline: models, configuration, troubleshooting |
| [Benefit catalogue](knowledge_base/README.md) | File by file, and how the parts relate |
| [API reference](docs/API.md) | Generated from [`openapi.json`](docs/openapi.json) |
| [`.env.example`](.env.example) | Every setting, with where to get each value |

<p align="center">◇ ─────── ✦ ─────── ◇</p>

<a id="fictional"></a>

## 9 ⋄ 𝐄𝐯𝐞𝐫𝐲𝐭𝐡𝐢𝐧𝐠 𝐡𝐞𝐫𝐞 𝐢𝐬 𝐟𝐢𝐜𝐭𝐢𝐨𝐧𝐚𝐥

Sawt Assurance is an invented insurer. Every member, provider, policy number, licence number and tariff is
synthetic, and procedure codes use a deliberately fictional `SP-#####` scheme rather than CPT. The structure
follows UAE health-insurance practice, so the rules behave believably: DHA and DOH mandated cover, tiered
networks, co-payments, pre-authorisation thresholds, waiting periods. **None of it is real policy, and none of
it may be used for a real authorisation decision.**

<div align="center">

<br>

𝘈 𝘷𝘰𝘪𝘤𝘦 𝘈𝘐 𝘱𝘳𝘦-𝘢𝘶𝘵𝘩𝘰𝘳𝘪𝘴𝘢𝘵𝘪𝘰𝘯 𝘳𝘦𝘧𝘦𝘳𝘦𝘯𝘤𝘦 𝘴𝘺𝘴𝘵𝘦𝘮

Sawt al-Tameen

</div>
