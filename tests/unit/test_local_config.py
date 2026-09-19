"""Local mode's configuration, and the promise that it needs none of the paid-channel credentials."""

import pytest

from preauth.infrastructure.settings import RuntimeMode, Settings, VoiceProvider
from preauth.local.config import LlmProvider, LocalSettings, SttProvider, TtsProvider


def test_defaults_to_the_elevenlabs_channel(monkeypatch):
    for name in ("PREAUTH_RUNTIME_MODE", "PREAUTH_GATEWAY_SECRET", "PREAUTH_VOICE_AGENT_TOKEN", "VOICE_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.runtime_mode is RuntimeMode.ELEVENLABS
    assert settings.voice_provider is VoiceProvider.ELEVENLABS
    assert settings.local_mode is False


def test_assemblyai_hosted_provider_is_selected_independently(monkeypatch):
    monkeypatch.delenv("PREAUTH_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("VOICE_PROVIDER", "AssemblyAI")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "test-aai-key")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID", "browser-agent")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID", "phone-agent")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_VOICE_ID", "alba")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_LLM_MODEL", "gemini-test")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_API_BASE", "https://agents.example.test/")
    monkeypatch.setenv("PREAUTH_ASSEMBLYAI_WS_URL", "wss://agents.example.test/ws")

    settings = Settings.from_env()

    assert settings.voice_provider is VoiceProvider.ASSEMBLYAI
    assert settings.assemblyai_enabled
    assert settings.assemblyai_api_key == "test-aai-key"
    assert settings.assemblyai_webhook_secret == "test-webhook-secret"
    assert settings.assemblyai_browser_agent_id == "browser-agent"
    assert settings.assemblyai_phone_agent_id == "phone-agent"
    assert settings.assemblyai_voice_id == "alba"
    assert settings.assemblyai_llm_model == "gemini-test"
    assert settings.assemblyai_api_base == "https://agents.example.test"
    assert settings.assemblyai_ws_url == "wss://agents.example.test/ws"


def test_an_unknown_voice_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "azure")
    with pytest.raises(ValueError, match="elevenlabs, assemblyai"):
        Settings.from_env()


def test_local_mode_is_selected_by_one_variable(monkeypatch):
    monkeypatch.setenv("PREAUTH_RUNTIME_MODE", "LOCAL")  # case-insensitive
    assert Settings.from_env().local_mode is True


def test_an_unknown_runtime_mode_is_rejected_with_the_allowed_values(monkeypatch):
    monkeypatch.setenv("PREAUTH_RUNTIME_MODE", "azure")
    with pytest.raises(ValueError, match="elevenlabs, local"):
        Settings.from_env()


def test_local_mode_requires_no_elevenlabs_credentials(monkeypatch):
    """Requirement, not coincidence: a local deployment must start with no paid-provider configuration."""
    for name in (
        "ELEVENLABS_API_KEY",
        "PREAUTH_PUBLIC_BASE_URL",
        "PREAUTH_ELEVENLABS_WEBHOOK_SECRET",
        "PREAUTH_VOICE_AGENT_TOKEN",
        "ASSEMBLYAI_API_KEY",
        "PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PREAUTH_RUNTIME_MODE", "local")

    settings = Settings.from_env()
    assert settings.local_mode
    assert settings.elevenlabs_webhook_secret is None
    assert settings.assemblyai_api_key is None
    assert settings.assemblyai_webhook_secret is None
    assert settings.voice_agent_token is None
    assert settings.database_url.startswith("sqlite:///")
    # And nothing in local mode's own configuration names a hosted provider.
    assert LocalSettings.from_env().llm_base_url.startswith("http://localhost")


def test_every_engine_is_configurable(monkeypatch):
    monkeypatch.setenv("PREAUTH_LOCAL_LLM_MODEL", "llama3.1:8b")
    monkeypatch.setenv("PREAUTH_LOCAL_LLM_BASE_URL", "http://ollama.internal:11434/")
    monkeypatch.setenv("PREAUTH_LOCAL_STT_MODEL", "medium")
    monkeypatch.setenv("PREAUTH_LOCAL_TTS_VOICE", "/voices/en_GB-alba-medium.onnx")
    monkeypatch.setenv("PREAUTH_LOCAL_PORT", "9123")

    settings = LocalSettings.from_env()
    assert settings.llm_model == "llama3.1:8b"
    assert settings.llm_base_url == "http://ollama.internal:11434"  # trailing slash trimmed
    assert settings.stt_model == "medium"
    assert settings.tts_voice_path == "/voices/en_GB-alba-medium.onnx"
    assert settings.port == 9123


def test_speech_can_be_switched_off_for_a_headless_machine(monkeypatch):
    monkeypatch.setenv("PREAUTH_LOCAL_STT_PROVIDER", "none")
    monkeypatch.setenv("PREAUTH_LOCAL_TTS_PROVIDER", "none")
    settings = LocalSettings.from_env()
    assert (settings.speech_input_enabled, settings.speech_output_enabled) == (False, False)


def test_unknown_providers_are_rejected(monkeypatch):
    monkeypatch.setenv("PREAUTH_LOCAL_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="ollama"):
        LocalSettings.from_env()


def test_only_local_providers_exist():
    """There is no hosted option to fall back to, by construction."""
    assert [p.value for p in LlmProvider] == ["ollama"]
    assert set(SttProvider) == {SttProvider.FASTER_WHISPER, SttProvider.DISABLED}
    assert set(TtsProvider) == {TtsProvider.PIPER, TtsProvider.DISABLED}


def test_the_context_window_is_set_explicitly_and_fits_the_prompt():
    """Ollama's 4096-token default nearly fits the prompt and tool schemas, and drops the oldest tokens when it
    does not — which would silently discard the safety instructions. So the window is configured, not inherited,
    and a diagnostic fails loudly if it is ever set too small."""
    from preauth.local.diagnostics import FAIL, OK, check_context_window

    assert LocalSettings().llm_context_tokens >= 8192
    assert check_context_window(LocalSettings()).status == OK

    too_small = LocalSettings(llm_context_tokens=4096)
    undersized = check_context_window(too_small)
    assert undersized.status == FAIL
    assert "PREAUTH_LOCAL_LLM_CONTEXT_TOKENS" in undersized.fix


def test_the_context_window_reaches_the_model_request():
    import httpx

    from preauth.local.llm import OllamaChatModel

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    settings = LocalSettings(llm_context_tokens=12345)
    model = OllamaChatModel(settings)
    model._client = httpx.Client(transport=httpx.MockTransport(handler), base_url=settings.llm_base_url)
    model.chat([{"role": "user", "content": "hi"}], [])
    assert captured["options"]["num_ctx"] == 12345
