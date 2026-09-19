"""Startup diagnostics for local mode.

Answers one question: what, on this machine, is still missing before a local call can be taken? Every failing
check carries the exact command that fixes it. Nothing here downloads anything — a multi-gigabyte model download
is always the operator's explicit decision, so a missing model is reported, not fetched.
"""

import json
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from preauth.local.config import LocalSettings, SttProvider, TtsProvider
from preauth.local.errors import LocalModeError

OK, WARN, FAIL = "ok", "warn", "fail"

# Whisper weights are published under this account and cached by name.
_WHISPER_REPO = "Systran/faster-whisper-{model}"
_HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "fix": self.fix}


def _ok(name: str, detail: str) -> Check:
    return Check(name, OK, detail)


def _fail(name: str, detail: str, fix: str) -> Check:
    return Check(name, FAIL, detail, fix)


def _warn(name: str, detail: str, fix: str) -> Check:
    return Check(name, WARN, detail, fix)


# --------------------------------------------------------------------------- individual checks


def check_python() -> Check:
    version = ".".join(str(p) for p in sys.version_info[:3])
    if sys.version_info < (3, 12):
        return _fail("python", f"Python {version} is too old", "install Python 3.12 or newer")
    return _ok("python", f"Python {version}")


def check_database(database_url: str) -> Check:
    from sqlalchemy import text

    from preauth.infrastructure.db.session import build_engine

    try:
        engine = build_engine(database_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as e:
        return _fail("database", f"Cannot connect: {str(e)[:160]}", "check PREAUTH_DATABASE_URL")
    return _ok("database", database_url)


def check_migrations(database_url: str) -> Check:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from preauth.infrastructure.db.session import build_engine

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    try:
        engine = build_engine(database_url)
        with engine.connect() as connection:
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        engine.dispose()
    except Exception:
        return _fail("migrations", "The schema has not been created", "uv run alembic upgrade head")
    if current != head:
        return _fail("migrations", f"At {current}, head is {head}", "uv run alembic upgrade head")
    return _ok("migrations", f"at head ({head})")


def check_seed(database_url: str) -> Check:
    from preauth.infrastructure.db.session import build_engine, build_session_factory
    from preauth.seed.catalogue import is_loaded

    try:
        factory = build_session_factory(build_engine(database_url))
        with factory() as session:
            loaded = is_loaded(session)
    except Exception as e:
        return _fail("seed data", f"Cannot read the catalogue: {str(e)[:160]}", "uv run alembic upgrade head")
    if not loaded:
        return _fail("seed data", "The UAE catalogue is not loaded", "uv run python -m preauth.seed --scenarios")
    return _ok("seed data", "UAE catalogue loaded from knowledge_base/")


def check_llm(settings: LocalSettings) -> list[Check]:
    if shutil.which("ollama") is None:
        return [
            _warn(
                "ollama binary",
                "ollama is not on PATH (fine if it runs elsewhere, e.g. in Docker)",
                "install it from https://ollama.com/download",
            ),
            *_llm_service(settings),
        ]
    return [_ok("ollama binary", shutil.which("ollama")), *_llm_service(settings)]


def _llm_service(settings: LocalSettings) -> list[Check]:
    from preauth.local.llm import build_chat_model

    model = build_chat_model(settings)
    try:
        model.check()
    except LocalModeError as e:
        fix = e.details.get("pull_it") or e.details.get("start_it") or e.details.get("install") or "see the README"
        return [_fail("local LLM", e.message, fix)]
    return [_ok("local LLM", model.name)]


def check_context_window(settings: LocalSettings) -> Check:
    """The system prompt and the three tool schemas must fit, with room left for the conversation.

    Worth its own check: when they do not fit, Ollama drops the oldest tokens, and the oldest tokens are the
    safety instructions. The failure is silent and looks like a badly behaved model.
    """
    from preauth.agent_tools.toolbox import TOOLS
    from preauth.local.prompt import system_prompt
    from preauth.local.tools import llm_tool_definitions

    described = [
        {"name": t.name, "description": t.description, "input_schema": t.input_model.model_json_schema()}
        for t in TOOLS
    ]
    # ~4 characters per token is close enough to size a window; this is a guard rail, not a tokeniser.
    fixed = (len(system_prompt()) + len(json.dumps(llm_tool_definitions(described)))) // 4
    needed = fixed + 2000  # room for the dialogue and the tool results it accumulates
    if settings.llm_context_tokens < needed:
        return _fail(
            "context window",
            f"the prompt and tool schemas need about {fixed} tokens; {settings.llm_context_tokens} leaves no "
            "room for the call",
            f"PREAUTH_LOCAL_LLM_CONTEXT_TOKENS={max(8192, needed)}",
        )
    return _ok("context window", f"{settings.llm_context_tokens} tokens, prompt and tools use about {fixed}")


def check_stt(settings: LocalSettings) -> Check:
    if settings.stt_provider is SttProvider.DISABLED:
        return _warn("speech to text", "disabled", "unset PREAUTH_LOCAL_STT_PROVIDER to enable the microphone")
    try:
        import faster_whisper  # noqa: F401
    except ModuleNotFoundError:
        return _fail("speech to text", "faster-whisper is not installed", "uv sync --extra local-voice")
    if settings.stt_model_dir:
        exists = Path(settings.stt_model_dir).is_dir()
        return (
            _ok("speech to text", f"local model directory {settings.stt_model_dir}")
            if exists
            else _fail("speech to text", f"{settings.stt_model_dir} is not a directory", "fix PREAUTH_LOCAL_STT_MODEL_DIR")
        )
    repo = _WHISPER_REPO.format(model=settings.stt_model)
    cached = _HF_CACHE / ("models--" + repo.replace("/", "--"))
    if not cached.is_dir():
        # Not a failure: the model downloads itself on first use. It is a warning because that first use needs
        # the Internet, and offline operation is a requirement of local mode.
        return _warn(
            "speech to text",
            f"faster-whisper installed; the {settings.stt_model!r} weights are not cached yet",
            f'uv run python -c "from faster_whisper import WhisperModel; WhisperModel(\'{settings.stt_model}\')"',
        )
    return _ok("speech to text", f"faster-whisper {settings.stt_model} (cached)")


def check_tts(settings: LocalSettings) -> Check:
    if settings.tts_provider is TtsProvider.DISABLED:
        return _warn("text to speech", "disabled", "unset PREAUTH_LOCAL_TTS_PROVIDER to enable spoken replies")
    try:
        import piper  # noqa: F401
    except ModuleNotFoundError:
        return _fail("text to speech", "piper-tts is not installed", "uv sync --extra local-voice")
    path = settings.tts_voice_path
    if not path:
        return _fail(
            "text to speech",
            "PREAUTH_LOCAL_TTS_VOICE is not set",
            "uv run python -m piper.download_voices en_GB-alba-medium",
        )
    if not Path(path).is_file():
        return _fail("text to speech", f"voice file {path} does not exist", "download the voice, or fix the path")
    if not Path(f"{path}.json").is_file():
        return _fail("text to speech", f"{path}.json is missing", "keep the .onnx.json config beside the .onnx")
    return _ok("text to speech", f"piper {Path(path).name}")


def check_port(settings: LocalSettings) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        in_use = probe.connect_ex((settings.host, settings.port)) == 0
    if in_use:
        return _warn(
            "local port",
            f"{settings.host}:{settings.port} is already in use",
            "stop the other process, or set PREAUTH_LOCAL_PORT",
        )
    return _ok("local port", f"{settings.host}:{settings.port} is free")


def check_no_paid_credentials_required(settings: LocalSettings) -> Check:
    """Local mode requires no AssemblyAI key, public URL or webhook secret."""
    import os

    present = [
        name
        for name in ("ASSEMBLYAI_API_KEY", "PREAUTH_PUBLIC_BASE_URL", "PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET")
        if (os.environ.get(name) or "").strip()
    ]
    if present:
        return _ok("credentials", "local mode ignores hosted credentials: " + ", ".join(present))
    return _ok("credentials", "no paid API credentials are set, and local mode needs none")


# --------------------------------------------------------------------------- report


def run_checks(settings: LocalSettings, database_url: str) -> list[Check]:
    return [
        check_python(),
        check_database(database_url),
        check_migrations(database_url),
        check_seed(database_url),
        *check_llm(settings),
        check_context_window(settings),
        check_stt(settings),
        check_tts(settings),
        check_port(settings),
        check_no_paid_credentials_required(settings),
    ]


def summarise(checks: list[Check]) -> dict[str, Any]:
    return {
        "ready": not any(c.status == FAIL for c in checks),
        "failures": sum(1 for c in checks if c.status == FAIL),
        "warnings": sum(1 for c in checks if c.status == WARN),
        "checks": [c.as_dict() for c in checks],
    }
