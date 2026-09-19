import asyncio
import json

from preauth.agent_tools.voice_gateway import VoiceToolResponse
from preauth.api import assemblyai_bridge
from preauth.api.assemblyai_bridge import AssemblyAIToolCoordinator
from preauth.infrastructure import assemblyai_media_token
from preauth.infrastructure.settings import Settings, VoiceProvider


class FakeGateway:
    def __init__(self, response: VoiceToolResponse | None = None):
        self.response = response or VoiceToolResponse(ok=True, result={"case_id": "case-1"})
        self.calls = []

    def call(self, name, arguments, conversation_id=None):
        self.calls.append((name, arguments, conversation_id))
        return self.response


class FakeProvider:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(0)
            yield json.dumps(event)
        if self.error:
            raise self.error


class FakeClientSocket:
    def __init__(self, events):
        self.events = list(events)
        self.sent = []
        self.closed = []

    async def receive_json(self):
        if self.events:
            await asyncio.sleep(0)
            return self.events.pop(0)
        await asyncio.Future()

    async def send_json(self, event):
        self.sent.append(event)

    async def close(self, code=1000, reason=None):
        self.closed.append((code, reason))


def test_media_token_round_trip_and_expiry():
    token = assemblyai_media_token.issue("CA123", "media-secret", now=1_000, ttl_seconds=90)
    assert assemblyai_media_token.verify(token, "media-secret", now=1_090).call_sid == "CA123"
    try:
        assemblyai_media_token.verify(token, "media-secret", now=1_091)
    except assemblyai_media_token.MediaTokenInvalidError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("expired media token was accepted")


def test_media_token_rejects_tampering_and_the_wrong_secret():
    token = assemblyai_media_token.issue("CA123", "media-secret", now=1_000)
    for forged in (token + "x", token.replace("A", "B", 1), token):
        secret = "other-secret" if forged == token else "media-secret"
        try:
            assemblyai_media_token.verify(forged, secret, now=1_000)
        except assemblyai_media_token.MediaTokenInvalidError:
            pass
        else:
            raise AssertionError("forged media token was accepted")


def test_tool_result_waits_for_reply_done_and_uses_the_provider_session_id():
    gateway = FakeGateway()
    coordinator = AssemblyAIToolCoordinator(gateway)

    async def scenario():
        assert await coordinator.handle({"type": "session.ready", "session_id": "sess_123"}) == []
        assert await coordinator.handle(
            {"type": "tool.call", "call_id": "call_1", "name": "get_case_status", "arguments": {"x": 1}}
        ) == []
        return await coordinator.handle({"type": "reply.done"})

    results = asyncio.run(scenario())
    assert gateway.calls == [("get_case_status", {"x": 1}, "sess_123")]
    assert len(results) == 1
    assert results[0]["call_id"] == "call_1"
    assert results[0]["is_error"] is False
    assert json.loads(results[0]["result"]) == {
        "ok": True, "result": {"case_id": "case-1"}, "error": None, "guidance": None
    }


def test_interrupted_reply_discards_pending_tool_results():
    coordinator = AssemblyAIToolCoordinator(FakeGateway())

    async def scenario():
        await coordinator.handle(
            {"type": "tool.call", "call_id": "call_1", "name": "get_case_status", "arguments": {}}
        )
        assert await coordinator.handle({"type": "reply.done", "status": "interrupted"}) == []
        return await coordinator.handle({"type": "reply.done"})

    assert asyncio.run(scenario()) == []


def test_failed_tool_result_is_marked_as_an_error():
    gateway = FakeGateway(
        VoiceToolResponse(ok=False, error={"code": "NOT_FOUND", "message": "Missing", "details": {}})
    )
    coordinator = AssemblyAIToolCoordinator(gateway)

    async def scenario():
        await coordinator.handle({"type": "tool.call", "call_id": "call_1", "name": "lookup", "arguments": {}})
        return await coordinator.handle({"type": "reply.done"})

    result = asyncio.run(scenario())[0]
    assert result["is_error"] is True
    assert json.loads(result["result"])["error"]["code"] == "NOT_FOUND"


def test_browser_bridge_maps_pcm_audio_and_sanitises_ready_event(monkeypatch):
    provider = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_browser", "config": {"system_prompt": "private"}},
            {"type": "session.updated", "config": {"tools": [{"description": "private"}]}},
            {"type": "reply.audio", "data": "output-pcm"},
            {"type": "transcript.user", "text": "hello"},
        ]
    )
    client = FakeClientSocket([{"type": "input.audio", "audio": "input-pcm"}])
    monkeypatch.setattr(assemblyai_bridge, "connect", lambda *args, **kwargs: provider)
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="secret",
        assemblyai_browser_agent_id="agent_browser",
    )

    asyncio.run(assemblyai_bridge.bridge_browser(client, settings, FakeGateway()))

    assert provider.sent[0] == {
        "type": "session.update", "session": {"agent_id": "agent_browser"}
    }
    assert provider.sent[1]["type"] == "session.update"
    assert [tool["name"] for tool in provider.sent[1]["session"]["tools"]] == [
        "verify_caller", "check_coverage_rule", "log_transcript"
    ]
    assert all(tool["type"] == "function" for tool in provider.sent[1]["session"]["tools"])
    assert {"type": "input.audio", "audio": "input-pcm"} in provider.sent
    assert client.sent[0] == {"type": "session.ready", "session_id": "sess_browser"}
    assert provider.sent[0].get("Authorization") is None
    assert client.sent[1:] == [
        {"type": "reply.audio", "data": "output-pcm"},
        {"type": "transcript.user", "text": "hello"},
    ]


def test_twilio_bridge_maps_pcmu_audio_barge_in_and_tool_results(monkeypatch):
    provider = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_phone"},
            {"type": "session.updated"},
            {"type": "reply.audio", "data": "outbound-pcmu"},
            {"type": "input.speech.started"},
            {"type": "tool.call", "call_id": "call_1", "name": "lookup", "arguments": {"id": "1"}},
            {"type": "reply.done"},
        ]
    )
    twilio = FakeClientSocket(
        [
            {"event": "start", "start": {"callSid": "CA123", "streamSid": "MZ123"}},
            {"event": "media", "media": {"track": "inbound", "payload": "inbound-pcmu"}},
        ]
    )
    gateway = FakeGateway()
    monkeypatch.setattr(assemblyai_bridge, "connect", lambda *args, **kwargs: provider)
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="secret",
        assemblyai_phone_agent_id="agent_phone",
    )

    asyncio.run(assemblyai_bridge.bridge_twilio(twilio, settings, gateway, "CA123"))

    assert provider.sent[0] == {"type": "session.update", "session": {"agent_id": "agent_phone"}}
    assert provider.sent[1]["type"] == "session.update"
    assert all(tool["type"] == "function" for tool in provider.sent[1]["session"]["tools"])
    assert {"type": "input.audio", "audio": "inbound-pcmu"} in provider.sent
    result = next(event for event in provider.sent if event["type"] == "tool.result")
    assert result["call_id"] == "call_1" and result["is_error"] is False
    assert gateway.calls == [("lookup", {"id": "1"}, "sess_phone")]
    assert twilio.sent == [
        {"event": "media", "streamSid": "MZ123", "media": {"payload": "outbound-pcmu"}},
        {"event": "clear", "streamSid": "MZ123"},
    ]


def test_browser_bridge_resumes_the_same_provider_session_after_a_network_drop(monkeypatch):
    first = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_resume"},
            {"type": "session.updated"},
        ],
        error=OSError("connection dropped"),
    )
    resumed = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_resume"},
            {"type": "transcript.user", "text": "still connected"},
        ]
    )
    providers = iter([first, resumed])
    client = FakeClientSocket([])
    monkeypatch.setattr(assemblyai_bridge, "connect", lambda *args, **kwargs: next(providers))

    async def no_delay(_):
        return None

    monkeypatch.setattr(assemblyai_bridge.asyncio, "sleep", no_delay)
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="secret",
        assemblyai_browser_agent_id="agent_browser",
    )

    asyncio.run(assemblyai_bridge.bridge_browser(client, settings, FakeGateway()))

    assert first.sent[0] == {
        "type": "session.update",
        "session": {"agent_id": "agent_browser"},
    }
    assert resumed.sent[0] == {"type": "session.resume", "session_id": "sess_resume"}
    assert client.sent[-1] == {"type": "transcript.user", "text": "still connected"}


def test_twilio_bridge_resumes_without_losing_the_stream_identity(monkeypatch):
    first = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_phone_resume"},
            {"type": "session.updated"},
        ],
        error=OSError("connection dropped"),
    )
    resumed = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_phone_resume"},
            {"type": "reply.audio", "data": "resumed-pcmu"},
        ]
    )
    providers = iter([first, resumed])
    twilio = FakeClientSocket(
        [{"event": "start", "start": {"callSid": "CA123", "streamSid": "MZ123"}}]
    )
    monkeypatch.setattr(assemblyai_bridge, "connect", lambda *args, **kwargs: next(providers))

    async def no_delay(_):
        return None

    monkeypatch.setattr(assemblyai_bridge.asyncio, "sleep", no_delay)
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="secret",
        assemblyai_phone_agent_id="agent_phone",
    )

    asyncio.run(assemblyai_bridge.bridge_twilio(twilio, settings, FakeGateway(), "CA123"))

    assert resumed.sent[0] == {"type": "session.resume", "session_id": "sess_phone_resume"}
    assert twilio.sent == [
        {"event": "media", "streamSid": "MZ123", "media": {"payload": "resumed-pcmu"}}
    ]


def test_resume_attempts_are_bounded_and_preserve_the_original_session_id(monkeypatch):
    coordinator = AssemblyAIToolCoordinator(FakeGateway())
    attempts = []

    async def connect_once(session_id):
        attempts.append(session_id)
        if session_id is None:
            await coordinator.handle({"type": "session.ready", "session_id": "sess_bounded"})
        raise OSError("upstream unavailable")

    async def no_delay(_):
        return None

    monkeypatch.setattr(assemblyai_bridge.asyncio, "sleep", no_delay)
    try:
        asyncio.run(assemblyai_bridge._run_resumable(coordinator, connect_once))
    except assemblyai_bridge.AssemblyAIResumeError:
        pass
    else:
        raise AssertionError("unbounded AssemblyAI resume loop")

    assert attempts == [None, "sess_bounded", "sess_bounded", "sess_bounded"]


def test_resume_refusal_fails_closed_without_starting_a_fresh_session(monkeypatch):
    first = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_expired"},
            {"type": "session.updated"},
        ],
        error=OSError("connection dropped"),
    )
    refused = FakeProvider(
        [
            {
                "type": "session.error",
                "code": "session_expired",
                "message": "provider detail must not reach the client",
            }
        ]
    )
    providers = iter([first, refused])
    client = FakeClientSocket([])
    monkeypatch.setattr(assemblyai_bridge, "connect", lambda *args, **kwargs: next(providers))

    async def no_delay(_):
        return None

    monkeypatch.setattr(assemblyai_bridge.asyncio, "sleep", no_delay)
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="secret",
        assemblyai_browser_agent_id="agent_browser",
    )

    async def scenario():
        await assemblyai_bridge.close_after_bridge(
            client, assemblyai_bridge.bridge_browser(client, settings, FakeGateway())
        )

    asyncio.run(scenario())

    assert refused.sent[0] == {"type": "session.resume", "session_id": "sess_expired"}
    assert client.closed == [(1011, "Voice service unavailable")]


def test_tool_configuration_rejection_fails_closed_without_leaking_provider_detail(monkeypatch):
    provider = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_bad_tools"},
            {
                "type": "session.error",
                "code": "invalid_configuration",
                "message": "private schema rejection detail",
            },
        ]
    )
    client = FakeClientSocket([])
    monkeypatch.setattr(assemblyai_bridge, "connect", lambda *args, **kwargs: provider)
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="secret",
        assemblyai_browser_agent_id="agent_browser",
    )

    async def scenario():
        await assemblyai_bridge.close_after_bridge(
            client, assemblyai_bridge.bridge_browser(client, settings, FakeGateway())
        )

    asyncio.run(scenario())

    assert provider.sent[1]["session"]["tools"]
    assert client.closed == [(1011, "Voice service unavailable")]


def test_bridge_failures_close_the_client_without_leaking_upstream_details():
    client = FakeClientSocket([])

    async def failure():
        raise OSError("provider response contained private details")

    asyncio.run(assemblyai_bridge.close_after_bridge(client, failure()))
    assert client.closed == [(1011, "Voice service unavailable")]
