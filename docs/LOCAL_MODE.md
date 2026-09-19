# Local mode

The same pre-authorisation system, with every hosted dependency replaced by something that runs on your machine.
No API key, no cloud database, no telephony provider, no AssemblyAI account, and after the models are downloaded,
no Internet connection.

```
                              Voice
                                │
                 ┌──────────────┴──────────────┐
                 │                             │
          AssemblyAI channel             Local channel
       Voice Agent · Twilio          faster-whisper · Piper · Ollama
                 │                             │
                 └──────────────┬──────────────┘
                                │
                     verify_caller · check_coverage_rule · log_transcript
                                │
                           Application
                                │
                 ┌──────────────┼──────────────┐
                 │              │              │
              Rules          Cases          Review
                 │
           knowledge_base/
```

Only the top box changes. The rules engine, the recommendation engine, the case lifecycle, the database schema,
the authorisation policy and the human-review mechanism are one implementation shared by both channels, and
`tests/unit/test_architecture.py` fails if the local package ever imports `preauth.rules`,
`preauth.recommendation` or the case state machine.

## What runs where

| Concern | AssemblyAI hosted mode | Local mode |
|---|---|---|
| Speech recognition | Voice Agent realtime input with keyterms | faster-whisper (Whisper weights via CTranslate2) |
| Speech synthesis | AssemblyAI Voice Agent voice | Piper (ONNX neural voice, CPU) |
| Language model | AssemblyAI LLM Gateway | Ollama, any tool-calling model |
| Telephony | Twilio | your browser's microphone |
| Transcript arrives | post-call webhook, HMAC signed | written when the call ends, by the process that handled it |
| Tools | three webhook tools | the same three, called in process |
| Rules, cases, review, audit | identical | identical |

## Install

Three things beyond the Python project. Total download is about 5 GB, almost all of it the language model.

```bash
# 1. Python dependencies
uv sync --extra local --extra local-voice

# 2. Ollama, and one tool-calling model
#    https://ollama.com/download , then:
ollama serve &
ollama pull qwen2.5-coder:7b        # ~4.7 GB. qwen3:8b also works; set PREAUTH_LOCAL_LLM_MODEL

# 3. A Piper voice (~60 MB) — the .onnx.json config must stay beside the .onnx
uv run python -m piper.download_voices en_GB-alba-medium --data-dir ./models/piper
echo 'PREAUTH_LOCAL_TTS_VOICE=./models/piper/en_GB-alba-medium.onnx' >> .env

# 4. Whisper weights (~460 MB), fetched on first use — pull them now to be offline later
uv run python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

`uv sync --extra local` alone (no `local-voice`) is enough for the text agent and for typing into the browser
console. Add `local-voice` when you want the microphone and spoken replies.

## Check what is missing

```bash
./scripts/check_local.sh
```

```
Local mode readiness

  PASS  python            Python 3.12.13
  PASS  database          sqlite:///./preauth.db
  PASS  migrations        at head (0001)
  PASS  seed data         UAE catalogue loaded from knowledge_base/
  PASS  ollama binary     /usr/local/bin/ollama
  FAIL  local LLM         Ollama is running but the model 'qwen3:8b' is not pulled
                          fix: ollama pull qwen3:8b
  WARN  speech to text    faster-whisper installed; the 'small' weights are not cached yet
                          fix: uv run python -c "from faster_whisper import WhisperModel; WhisperModel('small')"
  PASS  text to speech    piper en_GB-alba-medium.onnx
  PASS  local port        127.0.0.1:8000 is free
  PASS  credentials       no paid API credentials are set, and local mode needs none
```

Every failing line carries the command that fixes it. Nothing is downloaded by the check itself. The same report
is available at `GET /api/v1/local/diagnostics` once the server is up.

## Run

```bash
./scripts/run_local.sh
```

Migrates, seeds on first run only, reports readiness, then serves:

- **`http://localhost:8000/local`** — the voice console
- `http://localhost:8000/docs` — the API, local routes included

The script is safe to rerun and downloads nothing.

### Text only

```bash
uv run python -m preauth.local_cli
```

The same runtime with the microphone and loudspeaker switched off, for a machine with no audio devices and for
deterministic development. `--script calls/example.txt` replays caller turns from a file.

## A call, end to end

```
microphone ──▶ POST /api/v1/local/conversations/{id}/audio   (raw webm/opus)
                     │
                     ▼
              faster-whisper ──▶ text
                     │
                     ▼
              Ollama (system prompt + dialogue + backend state reminder + 3 tool schemas)
                     │
         ┌───────────┴───────────┐
         │ tool call             │ no tool call
         ▼                       │
   VoiceToolGateway              │
   AgentToolbox                  │
   DeskService ──▶ rules ──▶ recommendation ──▶ review queue
         │                       │
         └───────────┬───────────┘
                     ▼
                Piper ──▶ WAV ──▶ browser audio
```

The model never touches the database, never evaluates a rule and never decides a case. It chooses which of three
tools to call and puts the tool's answer into words.

## The prompt

Local mode uses `voice/local_system_prompt.md` rather than the hosted `voice/system_prompt.md`. That is a size
decision, not a policy one: a 7B model on a CPU spends most of a turn re-reading its prompt, and the hosted one
is about twice as long. Every safety behaviour is carried over word for word — the AI disclosure, the opening
line, verification before policy detail, the tool-first rule, the refusal to issue a decision and the exact
sentence it says when pressed for one. `tests/unit/test_local_prompt.py` checks each clause individually and
fails if one is ever dropped to save tokens.

Neither prompt restates a benefit rule. Cover, limits, co-payments and document requirements come back from
`check_coverage_rule`; the prompt only says what to do with them. A test asserts that too.

## Human review is unchanged

The local agent acts as a `VOICE_AGENT` actor named `local-agent`. Every guardrail that applies to the hosted
agent applies to it, for the same reason:

1. **No decision tool exists.** Three tools; none approves, denies or finalises. Asking for one returns
   `TOOL_NOT_FOUND`.
2. **The review API rejects the actor.** Recording a decision requires `HUMAN_REVIEWER`.
3. **The state machine** admits only `HUMAN_REVIEWER` into `APPROVED` or `DENIED`.
4. **Verification gates cover.** `check_coverage_rule` needs a `verification_id` from `verify_caller`.
5. **Transcript before sign-off.** A case a local call touched cannot be decided until
   `POST /api/v1/local/conversations/{id}/finish` has stored the transcript; until then reviewers get
   `CALL_RECORD_PENDING`, exactly as they do while an AssemblyAI session timeline is outstanding.
6. **Wording.** If the model states a final approval or denial anyway, the sentence is removed before the caller
   hears it and replaced with the standard boundary line. This changes nothing about the case — it only stops the
   agent saying something the system did not do.

## Auditability

Every local call produces a row in the same immutable `call_records` table a telephone call produces, with
`platform = "local"`, and a `CALL_RECORDED` audit event on each case it touched. The record holds the turns with
timestamps, the tool calls and their outcomes, the recommendation, any escalation rule ids, and the case
reference. The agent's recommendation and the human's decision remain separate, append-only records.

## Logging

Structured JSON, one conversation id through the whole call:

```bash
PREAUTH_LOG_LEVEL=INFO ./scripts/run_local.sh 2>&1 | grep local_3f9c1a2b8d4e5f60
```

Events: `conversation_started`, `stt_completed`, `llm_started`, `tool_called`, `tool_completed`,
`recommendation_created` / `escalation`, `decision_language_blocked`, `llm_completed`, `tts_completed`,
`conversation_finished`. Secrets are never logged; a test asserts it.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `PREAUTH_RUNTIME_MODE` | `hosted` | `local` mounts the local channel and console |
| `PREAUTH_LOCAL_LLM_PROVIDER` | `ollama` | the only local provider implemented |
| `PREAUTH_LOCAL_LLM_MODEL` | `qwen2.5-coder:7b` | any tool-calling model Ollama serves |
| `PREAUTH_LOCAL_LLM_BASE_URL` | `http://localhost:11434` | |
| `PREAUTH_LOCAL_LLM_TIMEOUT_SECONDS` | `120` | raise it on a slow CPU; a 7B's first load can take a minute |
| `PREAUTH_LOCAL_LLM_TEMPERATURE` | `0.1` | phrasing only; facts come from tools |
| `PREAUTH_LOCAL_LLM_CONTEXT_TOKENS` | `8192` | Ollama's default of 4096 nearly fills with the prompt alone |
| `PREAUTH_LOCAL_SYSTEM_PROMPT` | `voice/local_system_prompt.md` | point it at `voice/system_prompt.md` if your model can take it |
| `PREAUTH_LOCAL_MAX_TOOL_ITERATIONS` | `4` | after this the model must speak to the caller |
| `PREAUTH_LOCAL_STT_PROVIDER` | `faster-whisper` | or `none` for a machine with no microphone |
| `PREAUTH_LOCAL_STT_MODEL` | `small` | `tiny`/`base` are faster, `medium` more accurate |
| `PREAUTH_LOCAL_STT_COMPUTE_TYPE` | `int8` | `float32` if you have the memory |
| `PREAUTH_LOCAL_STT_MODEL_DIR` | unset | a downloaded model directory, for a machine with no Internet |
| `PREAUTH_LOCAL_TTS_PROVIDER` | `piper` | or `none` for text-only replies |
| `PREAUTH_LOCAL_TTS_VOICE` | unset | path to a Piper `.onnx` |
| `PREAUTH_LOCAL_HOST` / `PREAUTH_LOCAL_PORT` | `127.0.0.1` / `8000` | |

Local mode requires no hosted-provider credential, public URL, webhook/media secret or voice-tool token. In
particular, it does not use `ASSEMBLYAI_API_KEY` or any hosted AssemblyAI agent configuration.

## Offline operation

Downloaded once, then never needed again:

| Asset | Size | Command |
|---|---|---|
| Ollama model | ~4.7 GB | `ollama pull qwen2.5-coder:7b` |
| Whisper weights | ~460 MB | `WhisperModel('small', device='cpu', compute_type='int8')` |
| Piper voice | ~60 MB | `python -m piper.download_voices en_GB-alba-medium --data-dir ./models/piper` |

After that the application makes no outbound request of its own: the only network traffic it generates is HTTP
to `localhost:11434`.

One caveat worth knowing. faster-whisper resolves its model through the Hugging Face hub, and with a bare model
name (`small`) it will still *try* to reach the hub before falling back to the cache — harmless with no network,
but it costs a timeout on the first transcription. Two ways to avoid it:

```bash
export PREAUTH_LOCAL_STT_MODEL_DIR=~/.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots/<id>
# or, more bluntly
export HF_HUB_OFFLINE=1
```

Piper reads its voice from the path you give it and never looks anything up. Ollama serves from local storage
once the model is pulled.

## Security

Running locally is not a reason to drop a boundary:

- Local routes bind to `127.0.0.1` by default.
- If `PREAUTH_GATEWAY_SECRET` is set, the local routes require `X-Gateway-Secret` like every other route. The
  console asks the operator for it and keeps it in `sessionStorage` for that tab; it is never written into the
  page, the JavaScript or a log.
- `.env` and `models/` are git-ignored. No secret appears in anything the browser downloads — a test downloads
  every asset and asserts it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `LOCAL_SERVICE_UNAVAILABLE`, "Ollama is not reachable" | `ollama serve` |
| `LOCAL_SERVICE_UNAVAILABLE`, "model … is not pulled" | `ollama pull <model>`; the error names it |
| "The local model did not answer within 120s" | first load of a 7B on CPU is slow; raise `PREAUTH_LOCAL_LLM_TIMEOUT_SECONDS` |
| Answers ignore the safety instructions | the context window is too small, so the oldest tokens — the system prompt — were dropped. `check_local` reports this; raise `PREAUTH_LOCAL_LLM_CONTEXT_TOKENS` |
| The agent answers but never calls a tool | the model is not tool-tuned. Calls printed as JSON are recovered automatically; if not, use `qwen3:8b` |
| `SPEECH_NOT_RECOGNISED` | nothing intelligible in the recording. It is refused rather than guessed — record again |
| `LOCAL_DEPENDENCY_MISSING`, faster-whisper / piper-tts | `uv sync --extra local-voice` |
| `LOCAL_DEPENDENCY_MISSING`, voice file | the error carries the `download_voices` command |
| No audio in the browser | Chrome blocks autoplay until you interact with the page; the text reply is already shown |
| The microphone button is disabled | `PREAUTH_LOCAL_STT_PROVIDER=none`, or faster-whisper is not installed |
| `CHANNEL_NOT_CONFIGURED` on a local route | the process is not in local mode: `PREAUTH_RUNTIME_MODE=local` |
| `CALL_RECORD_PENDING` when reviewing | the call has not been ended. Press "End call", or POST `…/finish` |
| `CONVERSATION_CLOSED` | the call already ended and its transcript is stored; start a new one |

## Tests

```bash
uv run pytest -q                                          # everything, no models needed
uv run pytest tests/integration/test_local_agent.py -q    # a local call, end to end
```

The suite never downloads a model: the language model, recogniser and synthesiser are replaced by doubles, so
what the tests exercise is the agent loop, the tool boundary and the guardrails against the real database, the
real catalogue and the real rules.

To exercise the models actually installed on this machine:

```bash
uv run python scripts/verify_local.py                  # a real call through Ollama
uv run python scripts/verify_local.py --with-speech    # each caller turn synthesised by Piper and heard by Whisper
```

It separates **checks** — guarantees the backend enforces, where a failure is a defect — from **observations**,
which are things a small local model may or may not get right on a given run.

## Speed, honestly

On a CPU-only machine, a 7B model spends most of a turn re-reading the prompt. Expect the first turn of a call
to take a minute or more while the model loads and the prompt is processed, and later turns to be faster because
Ollama caches the prefix. That is usable for the text agent and for a demonstration; it is not yet
conversational latency.

If you want it quicker, the model is one environment variable:

```bash
PREAUTH_LOCAL_LLM_MODEL=qwen3:1.7b ./scripts/run_local.sh    # much faster, less reliable at tool calling
```

Whisper `small` transcribes roughly 2× faster than real time on CPU; `base` or `tiny` are quicker and worse with
reference numbers. Piper is not a bottleneck — it synthesises about ten seconds of speech in under a second.
