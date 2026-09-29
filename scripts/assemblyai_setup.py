"""Create or update the English-only AssemblyAI Voice Agent resources.

Two stored agents share one source-controlled prompt. The browser agent uses 24 kHz PCM; the phone agent uses
Twilio's native 8 kHz PCMU. The bridge attaches client-side tools to each live session. Re-running updates in
place using ids in ``.assemblyai-state.json``.

Required for a live run:
    ASSEMBLYAI_API_KEY
    PREAUTH_PUBLIC_BASE_URL
    PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET
    PREAUTH_ASSEMBLYAI_VOICE_ID

Usage:
    python scripts/assemblyai_setup.py --dry-run
    python scripts/assemblyai_setup.py
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from preauth.infrastructure.assemblyai_client import AssemblyAIClient, AssemblyAIError

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".assemblyai-state.json"
DEFAULT_API_BASE = "https://agents.assemblyai.com"
WEBHOOK_PATH = "/api/v1/voice/assemblyai/post-call"
FIRST_MESSAGE = (
    "Sawt Assurance pre-authorisation line, this is an automated assistant. The call is recorded for audit. "
    "Who am I speaking with?"
)
KEYTERM_LIMIT = 100
WEBHOOK_SECRET_MIN_LENGTH = 32
WEBHOOK_SECRET_MAX_LENGTH = 256
TRANSCRIPTION_PROMPT = (
    "An English-language UAE health-insurance pre-authorisation business call. Callers state personal and "
    "organisation names, clinic and hospital names, dates of birth, AED amounts, ICD-10 codes, provider "
    "identifiers such as PRV-30011, policy identifiers such as POL-SA-2026-000001, procedure identifiers such "
    "as SP-20050, and onboarding references such as ONB-APP-2026-0007."
)


def _catalogue(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "knowledge_base" / name).read_text(encoding="utf-8"))


def keyterms() -> list[str]:
    """Domain terms whose transcription errors would change a workflow or identifier."""
    terms = [
        "pre-authorisation", "pre-authorization", "prior authorisation", "policy number", "provider number",
        "Emirates ID", "ICD-10", "eClaimLink", "Shafafiya", "DHA", "DOH", "AED", "co-payment",
        "co-insurance", "outpatient", "inpatient", "day surgery", "expedited", "network tier", "sub-limit",
        "onboarding", "clinical notes", "operative plan", "imaging report", "prior treatment record",
    ]
    for tier in _catalogue("policy_tiers.json")["tiers"]:
        terms += [tier["name"], tier["network"]["name"]]
    providers = _catalogue("network_providers.json")["providers"]
    terms += ["PRV", "POL-SA", "SP", "MBR", "ONB-APP", "PA", "CL"]
    terms += [provider["provider_id"] for provider in providers]
    terms += [provider["name"] for provider in providers]
    procedures = _catalogue("procedure_coverage.json")["procedures"]
    terms += [procedure["code"] for procedure in procedures]
    terms += [
        "arthroscopy", "meniscectomy", "cholecystectomy", "polysomnography", "rhinoplasty", "septoplasty",
        "sleeve gastrectomy", "angioplasty", "prostatectomy", "haemodialysis",
    ]
    return list(dict.fromkeys(terms))[:KEYTERM_LIMIT]


def valid_webhook_secret(secret: str) -> bool:
    """Match AssemblyAI's webhook-secret length and printable-ASCII contract."""
    return (
        WEBHOOK_SECRET_MIN_LENGTH <= len(secret) <= WEBHOOK_SECRET_MAX_LENGTH
        and secret.isascii()
        and secret.isprintable()
        and not any(character.isspace() for character in secret)
    )


def agent_payload(
    *,
    name: str,
    voice_id: str,
    encoding: str,
    sample_rate: int,
    interrupt_response: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "system_prompt": (ROOT / "voice" / "system_prompt.md").read_text(encoding="utf-8"),
        "greeting": FIRST_MESSAGE,
        "voice": {"voice_id": voice_id},
        "input": {
            "type": "audio",
            "format": {"encoding": encoding, "sample_rate": sample_rate},
            "keyterms": keyterms(),
            "transcription_mode": "balanced",
            "transcription_prompt": TRANSCRIPTION_PROMPT,
            "language_codes": ["en"],
            "voice_focus": "far-field",
            "voice_focus_threshold": 0.85,
            "turn_detection": {"interrupt_response": interrupt_response},
        },
        "output": {
            "type": "audio",
            "voice": voice_id,
            "format": {"encoding": encoding, "sample_rate": sample_rate},
            "volume": 100,
        },
        # Function tools are client-side session configuration. The server bridge attaches them after binding this
        # stored agent; keeping the stored list empty avoids converting them into provider-executed HTTP tools.
        "tools": [],
        # An empty list explicitly removes any stale custom-LLM override on PUT. AssemblyAI's managed
        # conversational model is part of the Voice Agent API and requires no separate model entitlement.
        "llm": [],
    }


def desired_agents(*, voice_id: str) -> dict[str, dict[str, Any]]:
    return {
        "browser": agent_payload(
            name="Sawt Assurance - Browser Pre-Authorisation",
            voice_id=voice_id,
            encoding="audio/pcm",
            sample_rate=24000,
            # Browser speaker output can leak into its microphone even with
            # acoustic echo cancellation. Browser calls use half-duplex turns.
            interrupt_response=False,
        ),
        "phone": agent_payload(
            name="Sawt Assurance - Phone Pre-Authorisation",
            voice_id=voice_id,
            encoding="audio/pcmu",
            sample_rate=8000,
            interrupt_response=True,
        ),
    }


def _load_state() -> dict[str, Any]:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _upsert_agents(
    client: AssemblyAIClient, state: dict[str, Any], payloads: dict[str, dict[str, Any]]
) -> None:
    agents = state.setdefault("agents", {})
    for channel, payload in payloads.items():
        agent_id = agents.get(channel)
        if agent_id:
            client.request("PUT", f"/v1/agents/{agent_id}", payload)
            print(f"agent: updated {channel} {agent_id}")
        else:
            created = client.request("POST", "/v1/agents", payload)
            agent_id = created.get("id")
            if not isinstance(agent_id, str) or not agent_id:
                raise AssemblyAIError("AssemblyAI created an agent without returning its id")
            agents[channel] = agent_id
            _save_state(state)
            print(f"agent: created {channel} {agent_id}")


def _upsert_webhooks(
    client: AssemblyAIClient, state: dict[str, Any], *, public_base_url: str, secret: str
) -> None:
    subscriptions = state.setdefault("webhook_subscriptions", {})
    for channel, agent_id in state["agents"].items():
        body = {
            "url": public_base_url + WEBHOOK_PATH,
            "events": ["session.completed"],
            "secret": secret,
            "agent_id": agent_id,
            "enabled": True,
        }
        subscription_id = subscriptions.get(channel)
        if subscription_id:
            update_body = {key: value for key, value in body.items() if key != "agent_id"}
            client.request("PATCH", f"/v1/webhook-subscriptions/{subscription_id}", update_body)
            print(f"webhook: updated {channel} {subscription_id}")
        else:
            created = client.request("POST", "/v1/webhook-subscriptions", body)
            subscription_id = created.get("id")
            if not isinstance(subscription_id, str) or not subscription_id:
                raise AssemblyAIError("AssemblyAI created a webhook subscription without returning its id")
            subscriptions[channel] = subscription_id
            _save_state(state)
            print(f"webhook: created {channel} {subscription_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print payloads without calling AssemblyAI")
    parser.add_argument("--voice-id", default=os.environ.get("PREAUTH_ASSEMBLYAI_VOICE_ID", ""))
    parser.add_argument("--api-base", default=os.environ.get("PREAUTH_ASSEMBLYAI_API_BASE", DEFAULT_API_BASE))
    args = parser.parse_args()

    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    base_url = os.environ.get("PREAUTH_PUBLIC_BASE_URL", "").rstrip("/")
    webhook_secret = os.environ.get("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET", "").strip()
    voice_id = args.voice_id.strip()

    if args.dry_run:
        payloads = desired_agents(voice_id=voice_id or "<ASSEMBLYAI_VOICE_ID>")
        print(json.dumps({
            "agents": payloads,
            "webhook_subscriptions": {
                channel: {
                    "url": (base_url or "https://YOUR-PUBLIC-URL") + WEBHOOK_PATH,
                    "events": ["session.completed"],
                    "secret": "<WEBHOOK_SECRET>",
                    "agent_id": f"<{channel}_agent_id>",
                    "enabled": True,
                }
                for channel in payloads
            },
        }, indent=2))
        return 0

    missing = [
        name for name, value in (
            ("ASSEMBLYAI_API_KEY", api_key),
            ("PREAUTH_PUBLIC_BASE_URL", base_url),
            ("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET", webhook_secret),
            ("PREAUTH_ASSEMBLYAI_VOICE_ID", voice_id),
        ) if not value
    ]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not base_url.startswith("https://"):
        print("PREAUTH_PUBLIC_BASE_URL must be a public https:// URL", file=sys.stderr)
        return 2
    if not valid_webhook_secret(webhook_secret):
        print(
            "PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET must contain 32-256 printable ASCII characters without whitespace",
            file=sys.stderr,
        )
        return 2

    state = _load_state()
    client = AssemblyAIClient(api_key, api_base=args.api_base)
    payloads = desired_agents(voice_id=voice_id)
    try:
        _upsert_agents(client, state, payloads)
        _upsert_webhooks(client, state, public_base_url=base_url, secret=webhook_secret)
    except AssemblyAIError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _save_state(state)
    print("\nDone.")
    print(f"Browser agent: {state['agents']['browser']}")
    print(f"Phone agent:   {state['agents']['phone']}")
    print(f"Webhook:       {base_url}{WEBHOOK_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
