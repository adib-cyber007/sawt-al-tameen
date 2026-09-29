import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_generated_api_docs_are_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_api_docs.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


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
    assert payload["agents"]["browser"]["llm"] == []
    assert "<ASSEMBLYAI_API_KEY>" not in result.stdout
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


def test_conversation_simulation_runs_from_a_fresh_database(tmp_path):
    database = tmp_path / "simulation.db"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "simulate_conversations.py"), "--quiet"],
        cwd=ROOT,
        env={**os.environ, "PREAUTH_DATABASE_URL": f"sqlite:///{database.as_posix()}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 scenarios" in result.stdout and "0 failed" in result.stdout
