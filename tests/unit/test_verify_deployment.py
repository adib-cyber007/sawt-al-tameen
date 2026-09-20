"""Deployment verification handles tunnel disconnects without duplicating writes."""

import http.client
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _module():
    path = ROOT / "scripts" / "verify_deployment.py"
    spec = importlib.util.spec_from_file_location("verify_deployment", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return b'{"status":"ok"}'


def test_safe_read_retries_remote_disconnect(monkeypatch):
    module = _module()
    calls = []

    def urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("edge closed")
        return _Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    status, body = module.Client("https://voice.example", "token", None).request("GET", "/health")

    assert status == 200
    assert body == {"status": "ok"}
    assert len(calls) == 2


def test_write_reports_remote_disconnect_without_retry(monkeypatch):
    module = _module()
    calls = []

    def urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        raise http.client.RemoteDisconnected("edge closed")

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)

    status, body = module.Client("https://voice.example", "token", None).request(
        "POST", "/api/v1/voice/tools/verify_caller", {}
    )

    assert status == 0
    assert body == "RemoteDisconnected: edge closed"
    assert len(calls) == 1
