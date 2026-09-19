import os
from dataclasses import dataclass, field
from enum import StrEnum


class RuntimeMode(StrEnum):
    """Which interaction channel this process serves. The application layer below is identical for both."""

    ELEVENLABS = "elevenlabs"
    LOCAL = "local"


class VoiceProvider(StrEnum):
    """Hosted voice platform selected during the migration window."""

    ELEVENLABS = "elevenlabs"
    ASSEMBLYAI = "assemblyai"


def normalise_database_url(url: str) -> str:
    """Hosted Postgres providers hand out postgres:// or postgresql:// URLs; use the installed psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./preauth.db"
    log_level: str = "INFO"
    # When set, every /api/v1 route except the voice channel requires header X-Gateway-Secret with this value.
    # Stands in for the authenticating gateway when the service is exposed on a public URL.
    gateway_secret: str | None = None
    # Bearer token the voice platform presents on server-tool calls. Voice tools are disabled when unset.
    voice_agent_token: str | None = None
    # The hosted provider is independent of local mode. ElevenLabs remains the rollback default until live
    # AssemblyAI validation is complete.
    voice_provider: VoiceProvider = VoiceProvider.ELEVENLABS
    # HMAC secret of the ElevenLabs post-call webhook. The webhook endpoint is disabled when unset.
    elevenlabs_webhook_secret: str | None = None
    # Which voice channel this process exposes. ``local`` additionally mounts the local agent and its browser UI;
    # it never changes the rules, cases, review or audit layers.
    runtime_mode: RuntimeMode = RuntimeMode.ELEVENLABS
    # Inbound Twilio calls (register-call). The endpoint is disabled unless all four are set; secrets stay out of repr.
    twilio_auth_token: str | None = field(default=None, repr=False)
    elevenlabs_api_key: str | None = field(default=None, repr=False)
    elevenlabs_agent_id: str | None = None
    assemblyai_api_key: str | None = field(default=None, repr=False)
    assemblyai_webhook_secret: str | None = field(default=None, repr=False)
    assemblyai_browser_agent_id: str | None = None
    assemblyai_phone_agent_id: str | None = None
    assemblyai_voice_id: str | None = None
    assemblyai_llm_model: str = "gemini-2.5-flash"
    assemblyai_api_base: str = "https://agents.assemblyai.com"
    assemblyai_ws_url: str = "wss://agents.assemblyai.com/v1/ws"
    # The https:// address Twilio is configured with; Twilio signs that URL, not the one behind the tunnel.
    public_base_url: str | None = None

    @property
    def local_mode(self) -> bool:
        return self.runtime_mode is RuntimeMode.LOCAL

    @property
    def assemblyai_enabled(self) -> bool:
        return not self.local_mode and self.voice_provider is VoiceProvider.ASSEMBLYAI

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=normalise_database_url(os.environ.get("PREAUTH_DATABASE_URL", cls.database_url)),
            log_level=os.environ.get("PREAUTH_LOG_LEVEL", cls.log_level),
            gateway_secret=_optional("PREAUTH_GATEWAY_SECRET"),
            voice_agent_token=_optional("PREAUTH_VOICE_AGENT_TOKEN"),
            voice_provider=_voice_provider(),
            elevenlabs_webhook_secret=_optional("PREAUTH_ELEVENLABS_WEBHOOK_SECRET"),
            runtime_mode=_runtime_mode(),
            twilio_auth_token=_optional("TWILIO_AUTH_TOKEN"),
            elevenlabs_api_key=_optional("ELEVENLABS_API_KEY"),
            elevenlabs_agent_id=_optional("PREAUTH_ELEVENLABS_AGENT_ID"),
            assemblyai_api_key=_optional("ASSEMBLYAI_API_KEY"),
            assemblyai_webhook_secret=_optional("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET"),
            assemblyai_browser_agent_id=_optional("PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID"),
            assemblyai_phone_agent_id=_optional("PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID"),
            assemblyai_voice_id=_optional("PREAUTH_ASSEMBLYAI_VOICE_ID"),
            assemblyai_llm_model=_optional("PREAUTH_ASSEMBLYAI_LLM_MODEL") or cls.assemblyai_llm_model,
            assemblyai_api_base=(
                _optional("PREAUTH_ASSEMBLYAI_API_BASE") or cls.assemblyai_api_base
            ).rstrip("/"),
            assemblyai_ws_url=_optional("PREAUTH_ASSEMBLYAI_WS_URL") or cls.assemblyai_ws_url,
            public_base_url=(_optional("PREAUTH_PUBLIC_BASE_URL") or "").rstrip("/") or None,
        )


def _runtime_mode() -> RuntimeMode:
    raw = (os.environ.get("PREAUTH_RUNTIME_MODE") or RuntimeMode.ELEVENLABS.value).strip().lower()
    try:
        return RuntimeMode(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in RuntimeMode)
        raise ValueError(f"PREAUTH_RUNTIME_MODE must be one of: {allowed} (got {raw!r})") from None


def _voice_provider() -> VoiceProvider:
    raw = (os.environ.get("VOICE_PROVIDER") or VoiceProvider.ELEVENLABS.value).strip().lower()
    try:
        return VoiceProvider(raw)
    except ValueError:
        allowed = ", ".join(p.value for p in VoiceProvider)
        raise ValueError(f"VOICE_PROVIDER must be one of: {allowed} (got {raw!r})") from None
