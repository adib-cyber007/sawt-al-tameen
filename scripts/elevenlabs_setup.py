"""Create or update the ElevenLabs agent, its server tools, secret and knowledge base.

Everything the agent needs comes from this repository: tool schemas from ``preauth.agent_tools`` (three tools, none
of which can decide a case), the system prompt from ``voice/system_prompt.md``, and the knowledge base from
``knowledge_base/`` — the same catalogue the rules engine decides from. Re-running updates in place (IDs are kept
in ``.elevenlabs-state.json``, which is git-ignored).

Required environment:
    ELEVENLABS_API_KEY          your ElevenLabs API key
    PREAUTH_PUBLIC_BASE_URL     public HTTPS URL of this backend, e.g. https://xyz.trycloudflare.com
    PREAUTH_VOICE_AGENT_TOKEN   the same token the backend is started with

Usage:
    uv run python scripts/elevenlabs_setup.py --dry-run   # print payloads, no network calls
    uv run python scripts/elevenlabs_setup.py             # create/update

Configured in the ElevenLabs dashboard instead (see docs/VOICE_AGENT.md): the visual workflow and per-node tool
scoping, the post-call webhook, evaluation criteria, agent tests, LLM cascading, and the phone number.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from preauth.agent_tools.elevenlabs import all_webhook_tool_configs

KNOWLEDGE_BASE = Path(__file__).resolve().parents[1] / "knowledge_base"

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".elevenlabs-state.json"
API = "https://api.elevenlabs.io"

AGENT_NAME = "Sawt Assurance - Provider Pre-Authorisation Line"
DEFAULT_LLM = "gemini-2.5-flash"
DEFAULT_TTS_MODEL = "eleven_v3_conversational"
# Voice Library premade voice with a neutral, professional register, suited to a B2B compliance line rather than
# a consumer-warm one. Override with --voice-id after listening to the preview in your workspace.
DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
# Keyterm biasing works best on a focused list; the most collision-prone terms come first.
KEYTERM_LIMIT = 100
# Calls on our own Twilio number arrive through register-call, which ElevenLabs requires to use μ-law 8000 Hz in
# both directions (Twilio's native telephony format). Browser test calls still work at this format, at phone quality.
DEFAULT_AUDIO_FORMAT = "ulaw_8000"

FIRST_MESSAGE_EN = (
    "Sawt Assurance pre-authorisation line, this is an automated assistant. The call is recorded for audit. "
    "Who am I speaking with?"
)


def _catalogue(name: str) -> dict[str, Any]:
    return json.loads((KNOWLEDGE_BASE / name).read_text(encoding="utf-8"))


def keyterms() -> list[str]:
    """Scribe keyterm biasing: the codes, names and formats callers say aloud and generic models mishear."""
    terms = [
        "pre-authorisation", "pre-authorization", "prior authorisation", "policy number", "provider number",
        "Emirates ID", "ICD-10", "eClaimLink", "Shafafiya", "DHA", "DOH", "AED", "co-payment", "co-insurance",
        "outpatient", "inpatient", "day surgery", "expedited", "network tier", "sub-limit", "onboarding",
        "clinical notes", "operative plan", "imaging report", "prior treatment record",
    ]
    # Tier names and the network they map to.
    for tier in _catalogue("policy_tiers.json")["tiers"]:
        terms += [tier["name"], tier["network"]["name"]]
    # Every procedure code, plus the distinctive words in their names.
    procedures = _catalogue("procedure_coverage.json")["procedures"]
    terms += [p["code"] for p in procedures]
    terms += [
        "arthroscopy", "meniscectomy", "cholecystectomy", "polysomnography", "rhinoplasty", "septoplasty",
        "sleeve gastrectomy", "angioplasty", "prostatectomy", "polysomnography", "haemodialysis",
    ]
    # Provider numbers from the directory.
    terms += [p["provider_id"] for p in _catalogue("network_providers.json")["providers"]]
    # Identifier formats callers read out.
    terms += ["PRV", "POL-SA", "SP", "MBR", "ONB-APP", "PA", "CL"]
    # Provider names are added last: they are the least likely to be misheard in a way that breaks the flow, and
    # keyterm lists work best kept short.
    terms += [p["name"] for p in _catalogue("network_providers.json")["providers"]]
    return list(dict.fromkeys(terms))[:KEYTERM_LIMIT]


def knowledge_base_documents() -> dict[str, str]:
    """The knowledge base is knowledge_base/, the same catalogue the rules engine decides from.

    The per-tier schedules and the escalation rules carry the sections the tools cite, so a citation the agent
    reads out always resolves to a document it can retrieve.
    """
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(KNOWLEDGE_BASE.glob("*"))
        if path.suffix in (".md", ".json")
    }


def system_tool(name: str) -> dict[str, Any]:
    return {"type": "system", "name": name, "description": "", "params": {"system_tool_type": name}}


def agent_payload(
    *, tool_ids: list[str], knowledge_base: list[dict[str, str]], llm: str, tts_model: str, voice_id: str,
    audio_format: str = DEFAULT_AUDIO_FORMAT,
) -> dict[str, Any]:
    return {
        "name": AGENT_NAME,
        "tags": ["preauthorisation", "insurance", "uae", "b2b"],
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE_EN,
                "language": "en",
                "prompt": {
                    "prompt": (ROOT / "voice" / "system_prompt.md").read_text(encoding="utf-8"),
                    "llm": llm,
                    "temperature": 0.1,
                    "tool_ids": tool_ids,
                    "built_in_tools": {
                        "end_call": system_tool("end_call"),
                    },
                    "knowledge_base": knowledge_base,
                },
            },
            "asr": {"keywords": keyterms(), "user_input_audio_format": audio_format},
            "tts": {"voice_id": voice_id, "model_id": tts_model, "agent_output_audio_format": audio_format},
            "language_presets": {},
        },
    }


class ElevenLabs:
    def __init__(self, api_key: str):
        self._key = api_key

    def request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode(),
            headers={"xi-api-key": self._key, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise SystemExit(f"ElevenLabs API {method} {path} failed with {e.code}: {detail}") from None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print payloads without calling the API")
    parser.add_argument("--llm", default=DEFAULT_LLM)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID)
    parser.add_argument(
        "--audio-format", default=DEFAULT_AUDIO_FORMAT,
        help="agent input and output audio format; ulaw_8000 is required for Twilio register-call "
             "(use pcm_16000 only if the agent will never take phone calls)",
    )
    args = parser.parse_args()
    documents = knowledge_base_documents()

    base_url = os.environ.get("PREAUTH_PUBLIC_BASE_URL", "").rstrip("/")
    token = os.environ.get("PREAUTH_VOICE_AGENT_TOKEN", "")
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if args.dry_run:
        base_url = base_url or "https://YOUR-PUBLIC-URL"
        tools = all_webhook_tool_configs(base_url=base_url, authorization_secret_id="<secret_id>")
        kb = [{"type": "text", "name": n, "id": "<document_id>", "usage_mode": "auto"} for n in documents]
        print(json.dumps({"tools": tools, "agent": agent_payload(
            tool_ids=["<tool_id>"] * len(tools), knowledge_base=kb,
            llm=args.llm, tts_model=args.tts_model, voice_id=args.voice_id, audio_format=args.audio_format,
        )}, indent=2, ensure_ascii=False))
        return 0

    missing = [n for n, v in [("ELEVENLABS_API_KEY", api_key), ("PREAUTH_PUBLIC_BASE_URL", base_url),
                              ("PREAUTH_VOICE_AGENT_TOKEN", token)] if not v]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not base_url.startswith("https://"):
        print("PREAUTH_PUBLIC_BASE_URL must be an https:// URL reachable from the internet", file=sys.stderr)
        return 2

    client = ElevenLabs(api_key)
    state: dict[str, Any] = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}

    # 1. Secret holding the Authorization header value (recreated when the token changes).
    if state.get("secret_token_digest") != _digest(token):
        secret = client.request(
            "POST", "/v1/convai/secrets",
            {"type": "new", "name": f"preauth_voice_bearer_{_digest(token)[:8]}", "value": f"Bearer {token}"},
        )
        state["secret_id"] = secret["secret_id"]
        state["secret_token_digest"] = _digest(token)
        print(f"secret: created {state['secret_id']}")

    # 2. Server tools.
    tool_ids = state.setdefault("tool_ids", {})
    for config in all_webhook_tool_configs(base_url=base_url, authorization_secret_id=state["secret_id"]):
        name = config["name"]
        if name in tool_ids:
            client.request("PATCH", f"/v1/convai/tools/{tool_ids[name]}", {"tool_config": config})
            print(f"tool: updated {name}")
        else:
            tool_ids[name] = client.request("POST", "/v1/convai/tools", {"tool_config": config})["id"]
            print(f"tool: created {name}")
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # 3. Knowledge base (a new document is uploaded only when its content changed).
    kb_state = state.setdefault("knowledge_base", {})
    for name, text in documents.items():
        if kb_state.get(name, {}).get("digest") != _digest(text):
            created = client.request("POST", "/v1/convai/knowledge-base/text", {"name": name, "text": text})
            kb_state[name] = {"id": created["id"], "digest": _digest(text)}
            print(f"knowledge base: uploaded {name}")
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    knowledge_base = [
        {"type": "text", "name": name, "id": entry["id"], "usage_mode": "auto"}
        for name, entry in kb_state.items()
        if name in documents
    ]

    # 4. Agent.
    payload = agent_payload(
        tool_ids=[tool_ids[c["name"]] for c in all_webhook_tool_configs(base_url=base_url, authorization_secret_id="")],
        knowledge_base=knowledge_base,
        llm=args.llm,
        tts_model=args.tts_model,
        voice_id=args.voice_id,
        audio_format=args.audio_format,
    )
    if "agent_id" in state:
        client.request("PATCH", f"/v1/convai/agents/{state['agent_id']}", payload)
        print(f"agent: updated {state['agent_id']}")
    else:
        state["agent_id"] = client.request("POST", "/v1/convai/agents/create", payload)["agent_id"]
        print(f"agent: created {state['agent_id']}")
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(
        f"\nDone. Agent audio: {args.audio_format} in and out (Twilio register-call needs ulaw_8000).\n"
        "Remaining dashboard steps (docs/VOICE_AGENT.md): post-call webhook -> "
        f"{base_url}/api/v1/voice/elevenlabs/post-call, workflow + tool scoping, evaluation criteria, tests.\n"
        f"Twilio number: Voice Configuration -> A call comes in -> Webhook, HTTP POST -> "
        f"{base_url}/api/v1/voice/twilio/inbound\n"
        f"Test in the browser: https://elevenlabs.io/app/talk-to?agent_id={state['agent_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
