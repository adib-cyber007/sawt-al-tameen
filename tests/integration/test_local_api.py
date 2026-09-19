"""The local HTTP channel: what it exposes, what it refuses, and what it never requires."""

import base64

import pytest
from fastapi.testclient import TestClient

from preauth.api.app import create_app
from preauth.infrastructure.settings import RuntimeMode, Settings
from tests.integration.test_local_agent import VERIFY
from tests.local_fakes import calls, says


@pytest.fixture
def local_client(local):
    def build(*replies, settings: Settings | None = None, **kwargs):
        harness = local(*replies, **kwargs)
        harness.runtime.synthesizer = harness.synthesizer
        harness.runtime.transcriber = harness.transcriber
        app = create_app(
            harness.runtime.services,
            settings or Settings(runtime_mode=RuntimeMode.LOCAL),
            harness.runtime,
        )
        return harness, TestClient(app, raise_server_exceptions=False)

    return build


def test_local_routes_are_absent_unless_the_process_runs_in_local_mode(services):
    """The single branch local mode introduces: no runtime, no routes."""
    with TestClient(create_app(services)) as client:
        assert client.post("/api/v1/local/conversations").status_code == 404
        assert client.get("/local").status_code == 404
        # The ElevenLabs channel is untouched and still present.
        assert "/api/v1/voice/tools/{tool_name}" in client.app.openapi()["paths"]


def test_local_mode_starts_with_no_elevenlabs_credentials(local_client):
    """Acceptance criterion: no API key, no public URL, no webhook secret, no telephony."""
    settings = Settings(runtime_mode=RuntimeMode.LOCAL)
    assert (settings.voice_agent_token, settings.elevenlabs_webhook_secret) == (None, None)

    _, client = local_client(says("Hello."), settings=settings)
    started = client.post("/api/v1/local/conversations")
    assert started.status_code == 200, started.text
    assert started.json()["conversation_id"].startswith("local_")

    # And the ElevenLabs endpoints correctly report themselves as unconfigured rather than half-working.
    assert client.post("/api/v1/voice/tools/verify_caller", json={}).json()["error"]["code"] == (
        "CHANNEL_NOT_CONFIGURED"
    )


def test_the_browser_console_and_its_assets_are_served(local_client):
    _, client = local_client()
    page = client.get("/local")
    assert page.status_code == 200
    assert "Sawt Assurance" in page.text
    assert client.get("/local/assets/app.js").status_code == 200
    assert client.get("/local/assets/styles.css").status_code == 200


def test_no_secret_appears_in_anything_the_browser_downloads(local_client):
    """The console holds no credential: if the deployment sets one, the page asks the operator for it."""
    _, client = local_client(settings=Settings(runtime_mode=RuntimeMode.LOCAL, gateway_secret="s3cret-value"))
    for path in ("/local", "/local/assets/app.js", "/local/assets/styles.css"):
        assert "s3cret-value" not in client.get(path, headers={"X-Gateway-Secret": "s3cret-value"}).text


def test_a_gateway_secret_is_enforced_on_the_local_routes_too(local_client):
    """Running locally is not a reason to drop an authentication boundary the deployment configured."""
    _, client = local_client(says("Hello."), settings=Settings(runtime_mode=RuntimeMode.LOCAL, gateway_secret="s3"))
    assert client.post("/api/v1/local/conversations").json()["error"]["code"] == "GATEWAY_SECRET_INVALID"
    ok = client.post("/api/v1/local/conversations", headers={"X-Gateway-Secret": "s3"})
    assert ok.status_code == 200


def test_a_typed_turn_runs_the_tools_and_returns_the_case_state(local_client):
    harness, client = local_client(calls("verify_caller", **VERIFY), says("Verified. What procedure?"))
    conversation = client.post("/api/v1/local/conversations").json()["conversation_id"]

    turn = client.post(
        f"/api/v1/local/conversations/{conversation}/text", json={"text": "Aisha at Al Hudaiba, PRV-30011."}
    ).json()
    assert turn["tool_calls"][0] == {
        "tool": "verify_caller", "ok": True, "arguments": VERIFY, "error_code": None
    }
    assert turn["state"]["verified"] is True
    # Spoken back as audio, because this deployment has a synthesiser configured.
    assert base64.b64decode(turn["audio"]).startswith(b"RIFF")


def test_a_spoken_turn_is_transcribed_from_the_raw_request_body(local_client):
    harness, client = local_client(says("Verified."), heard="Aisha at Al Hudaiba, PRV-30011")
    conversation = client.post("/api/v1/local/conversations").json()["conversation_id"]

    turn = client.post(
        f"/api/v1/local/conversations/{conversation}/audio",
        content=b"fake-webm-bytes",
        headers={"Content-Type": "audio/webm"},
    ).json()
    assert harness.transcriber.calls == [b"fake-webm-bytes"]
    assert turn["heard"] == "Aisha at Al Hudaiba, PRV-30011"
    assert turn["heard_language"] == "en"


def test_an_unknown_conversation_is_a_404_not_a_new_call(local_client):
    _, client = local_client()
    response = client.post("/api/v1/local/conversations/local_0000000000000000/text", json={"text": "hi"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_finishing_a_call_over_http_stores_the_transcript(local_client):
    harness, client = local_client(calls("verify_caller", **VERIFY), says("Verified."))
    conversation = client.post("/api/v1/local/conversations").json()["conversation_id"]
    client.post(f"/api/v1/local/conversations/{conversation}/text", json={"text": "Aisha, PRV-30011."})

    finished = client.post(f"/api/v1/local/conversations/{conversation}/finish").json()
    assert finished["call_record_id"]
    assert [t["role"] for t in finished["transcript"]] == ["agent", "caller", "tool", "agent"]

    state = client.get(f"/api/v1/local/conversations/{conversation}").json()
    assert state["state"]["finished"] is True


def test_capabilities_tell_the_browser_which_engines_are_running(local_client):
    _, client = local_client()
    capabilities = client.get("/api/v1/local/capabilities").json()
    assert capabilities["speech_input"] is True and capabilities["speech_output"] is True
    assert capabilities["model"] == "scripted"


def test_diagnostics_report_what_is_missing_over_http(local_client):
    _, client = local_client()
    report = client.get("/api/v1/local/diagnostics").json()
    assert {"ready", "failures", "warnings", "checks"} <= report.keys()
    names = {c["name"] for c in report["checks"]}
    assert {"database", "migrations", "seed data", "local LLM", "speech to text", "text to speech"} <= names
    for check in report["checks"]:
        assert check["status"] == "ok" or check["fix"], check


def test_an_unavailable_local_engine_is_a_503_with_the_command_that_fixes_it(local_client):
    from preauth.local.errors import LocalServiceUnavailableError

    harness, client = local_client(says("Hello."))
    conversation = client.post("/api/v1/local/conversations").json()["conversation_id"]

    def unavailable(*_args, **_kwargs):
        raise LocalServiceUnavailableError("Ollama is not reachable", details={"start_it": "ollama serve"})

    harness.model.chat = unavailable
    response = client.post(f"/api/v1/local/conversations/{conversation}/text", json={"text": "hello"})
    assert response.status_code == 503
    assert response.json()["error"]["details"]["start_it"] == "ollama serve"


def test_unintelligible_audio_is_rejected_rather_than_guessed(local_client):
    from preauth.local.errors import SpeechNotRecognisedError

    harness, client = local_client(says("Hello."))
    conversation = client.post("/api/v1/local/conversations").json()["conversation_id"]

    def nothing_heard(*_args, **_kwargs):
        raise SpeechNotRecognisedError("No speech was recognised in that recording.")

    harness.transcriber.transcribe = nothing_heard
    response = client.post(f"/api/v1/local/conversations/{conversation}/audio", content=b"\x00\x00")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SPEECH_NOT_RECOGNISED"


def test_the_local_channel_never_exposes_a_decision_endpoint(local_client):
    _, client = local_client()
    local_paths = [p for p in client.app.openapi()["paths"] if p.startswith("/api/v1/local")]
    assert local_paths
    for path in local_paths:
        assert not any(w in path for w in ("approve", "deny", "decision", "finalise", "authorise"))
