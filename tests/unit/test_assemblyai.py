"""AssemblyAI tool schemas, stored-agent payloads and REST boundary."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from preauth.agent_tools.assemblyai import all_function_tool_configs
from preauth.agent_tools.toolbox import TOOLS
from preauth.infrastructure.assemblyai_client import AssemblyAIClient, AssemblyAIError

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_tool_converts_to_an_assemblyai_function_schema():
    configs = all_function_tool_configs()
    assert [config["name"] for config in configs] == [tool.name for tool in TOOLS]
    assert len(configs) == 3
    for config in configs:
        assert config["type"] == "function"
        assert "http" not in config
        assert config["description"]
        assert config["execution_mode"] == "interactive"
        assert 1 <= config["timeout_seconds"] <= 300
        schema = config["parameters"]
        encoded = json.dumps(schema)
        assert schema["type"] == "object"
        assert "$ref" not in encoded and "$defs" not in encoded and "anyOf" not in encoded
        assert set(schema["required"]) <= set(schema["properties"])
        for prop in schema["properties"].values():
            assert prop["description"]


def test_agent_payloads_use_channel_native_audio_and_one_tool_source():
    module = _load_script("assemblyai_setup.py")

    agents = module.desired_agents(voice_id="alba")
    assert set(agents) == {"browser", "phone"}
    assert agents["browser"]["input"]["format"] == {"encoding": "audio/pcm", "sample_rate": 24000}
    assert agents["phone"]["input"]["format"] == {"encoding": "audio/pcmu", "sample_rate": 8000}
    assert agents["browser"]["input"]["turn_detection"] == {"interrupt_response": False}
    assert agents["phone"]["input"]["turn_detection"] == {"interrupt_response": True}
    for payload in agents.values():
        assert payload["input"]["type"] == payload["output"]["type"] == "audio"
        assert payload["input"]["format"] == payload["output"]["format"]
        assert payload["tools"] == []
        assert payload["llm"] == []
        assert payload["input"]["keyterms"]
        assert payload["input"]["language_codes"] == ["en"]
        assert payload["input"]["transcription_mode"] == "balanced"
        assert payload["input"]["voice_focus"] == "far-field"
        assert "PRV-30011" in payload["input"]["transcription_prompt"]
        assert "never issue a final approval or denial" in payload["system_prompt"].lower()


def test_recognition_keyterms_prioritise_every_known_provider_identity():
    module = _load_script("assemblyai_setup.py")
    terms = module.keyterms()
    providers = json.loads(
        (ROOT / "knowledge_base" / "network_providers.json").read_text(encoding="utf-8")
    )["providers"]

    assert len(terms) <= module.KEYTERM_LIMIT
    assert all(provider["provider_id"] in terms for provider in providers)
    assert all(provider["name"] in terms for provider in providers)


def test_webhook_updates_do_not_send_immutable_agent_scope():
    module = _load_script("assemblyai_setup.py")
    calls = []

    class Client:
        def request(self, method, path, body):
            calls.append((method, path, body))
            return {}

    state = {
        "agents": {"browser": "agent_browser"},
        "webhook_subscriptions": {"browser": "subscription_browser"},
    }
    module._upsert_webhooks(
        Client(), state, public_base_url="https://voice.example", secret="s" * 32
    )

    assert calls == [(
        "PATCH",
        "/v1/webhook-subscriptions/subscription_browser",
        {
            "url": "https://voice.example/api/v1/voice/assemblyai/post-call",
            "events": ["session.completed"],
            "secret": "s" * 32,
            "enabled": True,
        },
    )]


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        ("s" * 32, True),
        ("s" * 256, True),
        ("s" * 31, False),
        ("s" * 257, False),
        ("s" * 31 + " ", False),
        ("s" * 31 + "é", False),
    ],
)
def test_webhook_secret_validation_matches_provider_contract(secret, expected):
    module = _load_script("assemblyai_setup.py")
    assert module.valid_webhook_secret(secret) is expected


@pytest.fixture
def upstream():
    seen: list[dict] = []
    reply = {"status": 200, "body": {"id": "agent_1"}}

    class Handler(BaseHTTPRequestHandler):
        def _handle(self):
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length) if length else b""
            seen.append({
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(raw) if raw else None,
            })
            body = json.dumps(reply["body"]).encode()
            self.send_response(reply["status"])
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen, reply
    server.shutdown()


def test_rest_client_uses_voice_agent_rest_auth_and_json(upstream):
    base, seen, _ = upstream
    result = AssemblyAIClient("test-key", api_base=base).request(
        "POST", "/v1/agents", {"name": "agent"}, query={"region": "us"}
    )
    assert result == {"id": "agent_1"}
    assert seen == [{
        "method": "POST",
        "path": "/v1/agents?region=us",
        "authorization": "test-key",
        "body": {"name": "agent"},
    }]


def test_rest_client_errors_do_not_leak_the_key(upstream):
    base, _, reply = upstream
    reply.update(status=401, body={"error": "invalid key"})
    with pytest.raises(AssemblyAIError) as raised:
        AssemblyAIClient("very-secret-key", api_base=base).request("GET", "/v1/agents")
    assert raised.value.status == 401
    assert "very-secret-key" not in str(raised.value)


def test_unreachable_rest_api_fails_boundedly():
    with pytest.raises(AssemblyAIError, match="could not be reached"):
        AssemblyAIClient("key", api_base="http://127.0.0.1:1", timeout_secs=1).request("GET", "/v1/agents")


def test_reconciliation_uses_the_documented_nested_session_cursor():
    reconcile = _load_script("assemblyai_reconcile.py")
    assert reconcile._next_cursor(
        {"has_more": True, "response_metadata": {"next_cursor": "page-2"}}
    ) == "page-2"
    assert reconcile._next_cursor(
        {"has_more": False, "response_metadata": {"next_cursor": "ignored"}}
    ) is None
    with pytest.raises(AssemblyAIError, match="omitted its next cursor"):
        reconcile._next_cursor({"has_more": True, "response_metadata": {}})
