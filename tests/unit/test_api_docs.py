import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_generated_api_docs_are_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_api_docs.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_elevenlabs_setup_dry_run_builds_payloads():
    import json

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "elevenlabs_setup.py"), "--dry-run"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload["tools"]) == 3
    agent = payload["agent"]["conversation_config"]
    assert "ar" in agent["language_presets"]
    assert agent["asr"]["keywords"]
    prompt = agent["agent"]["prompt"]["prompt"]
    assert "never issue a final approval or denial" in prompt.lower()
    assert "you identify yourself as an ai assistant" in prompt.lower()
    assert "verify_caller" in prompt and "check_coverage_rule" in prompt and "log_transcript" in prompt
    # The knowledge base is the catalogue the rules decide from, and nothing else.
    documents = {d["name"] for d in agent["agent"]["prompt"]["knowledge_base"]}
    assert "schedule-basic" in documents and "escalation_rules" in documents
    assert not any(d.startswith("plan-") for d in documents)


def test_assemblyai_setup_dry_run_builds_two_secret_free_payloads():
    import json

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "assemblyai_setup.py"), "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload["agents"]) == {"browser", "phone"}
    assert payload["agents"]["browser"]["input"]["format"]["encoding"] == "audio/pcm"
    assert payload["agents"]["phone"]["input"]["format"]["encoding"] == "audio/pcmu"
    assert "<ASSEMBLYAI_API_KEY>" in result.stdout
    assert "session.completed" in result.stdout


def test_database_url_normalisation():
    from preauth.infrastructure.settings import normalise_database_url

    assert normalise_database_url("postgres://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert normalise_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("sqlite:///./x.db") == "sqlite:///./x.db"


def test_uae_knowledge_base_is_current_and_consistent():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_uae_knowledge_base.py"), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
