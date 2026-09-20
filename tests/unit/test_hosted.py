"""Hosted launcher selection stays AssemblyAI-first without exercising live provider APIs."""

import argparse
import importlib.util
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def _module():
    path = ROOT / "scripts" / "hosted.py"
    spec = importlib.util.spec_from_file_location("hosted", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_environment_selects_hosted_mode_and_hides_tunnel_credentials():
    hosted = _module()
    env = hosted.backend_env(
        {
            "VOICE_PROVIDER": "assemblyai",
            "ASSEMBLYAI_API_KEY": "test-key",
            "NGROK_AUTHTOKEN": "ngrok-secret",
            "CLOUDFLARE_TUNNEL_TOKEN": "cloudflare-secret",
        }
    )
    assert env["PREAUTH_RUNTIME_MODE"] == "hosted"
    assert env["VOICE_PROVIDER"] == "assemblyai"
    assert env["ASSEMBLYAI_API_KEY"] == "test-key"
    assert "NGROK_AUTHTOKEN" not in env
    assert "CLOUDFLARE_TUNNEL_TOKEN" not in env


def test_provider_setup_dispatches_only_to_assemblyai(monkeypatch):
    hosted = _module()
    calls = []
    monkeypatch.setattr(hosted, "setup_assemblyai", lambda config: calls.append("assemblyai") or {"browser": "b", "phone": "p"})

    assert hosted.setup_provider({"VOICE_PROVIDER": "assemblyai"}) == {"browser": "b", "phone": "p"}
    assert calls == ["assemblyai"]


def test_windows_child_shutdown_does_not_require_posix_process_groups(monkeypatch):
    hosted = _module()
    calls = []

    class FakeProcess:
        pid = 42

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout):
            calls.append(("wait", timeout))

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(hosted, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        hosted.os,
        "killpg",
        lambda *_: (_ for _ in ()).throw(AssertionError("POSIX process groups are unavailable on Windows")),
        raising=False,
    )

    hosted._stop_process(FakeProcess())

    assert calls == ["terminate", ("wait", 10)]


def test_windows_child_shutdown_forces_kill_after_timeout(monkeypatch):
    hosted = _module()
    calls = []

    class FakeProcess:
        pid = 42

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("child", timeout)

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(hosted, "_IS_WINDOWS", True)
    hosted._stop_process(FakeProcess())

    assert calls == ["terminate", "kill"]


def test_assemblyai_preflight_generates_independent_secrets_and_loads_agent_ids(tmp_path, monkeypatch):
    hosted = _module()
    hosted.ENV_FILE = tmp_path / ".env"
    hosted.ENV_FILE.write_text("VOICE_PROVIDER=assemblyai\n")
    hosted.HOSTED_DIR = tmp_path / ".hosted"
    hosted.SECRETS_FILE = hosted.HOSTED_DIR / "secrets.env"
    monkeypatch.setattr(hosted, "port_free", lambda port: True)
    monkeypatch.setattr(
        hosted,
        "load_assemblyai_state",
        lambda: {"agents": {"browser": "agent_browser", "phone": "agent_phone"}},
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, **kwargs):
            assert (method, path) == ("GET", "/v1/agents")
            return {"agents": []}

    monkeypatch.setattr(hosted, "AssemblyAIClient", FakeClient)
    config = {
        "VOICE_PROVIDER": "assemblyai",
        "ASSEMBLYAI_API_KEY": "test-key",
        "PREAUTH_ASSEMBLYAI_VOICE_ID": "voice-english",
        "PREAUTH_PUBLIC_BASE_URL": "https://voice.example",
        "PREAUTH_HOSTED_PORT": "8123",
    }

    hosted.preflight(config, argparse.Namespace(no_tunnel=True))

    assert config["PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID"] == "agent_browser"
    assert config["PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID"] == "agent_phone"
    generated = hosted.read_env_file(hosted.SECRETS_FILE)
    assert all(
        generated.get(name)
        for name in (
            "PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET",
            "PREAUTH_ASSEMBLYAI_MEDIA_SECRET",
            "PREAUTH_VOICE_TOOL_TOKEN",
            "PREAUTH_GATEWAY_SECRET",
        )
    )
    assert len(set(generated.values())) == 4
