import asyncio
import json
import threading

import pytest

from preauth.agent_tools.voice_gateway import VoiceToolResponse
from preauth.api import assemblyai_bridge
from preauth.api.assemblyai_bridge import AssemblyAIToolCoordinator
from preauth.infrastructure import assemblyai_media_token
from preauth.infrastructure.settings import Settings, VoiceProvider


class FakeGateway:
    def __init__(self, response: VoiceToolResponse | None = None):
        self.response = response or VoiceToolResponse(ok=True, result={"case_id": "case-1"})
        self.calls = []
        self.call_ids = []

    def call(self, name, arguments, conversation_id=None, provider_call_id=None):
        self.calls.append((name, arguments, conversation_id))
        self.call_ids.append(provider_call_id)
        return self.response


def test_cancelling_bridge_pair_drains_both_audio_pumps():
    async def scenario():
        started = [asyncio.Event(), asyncio.Event()]
        stopped = []

        async def pump(index):
            started[index].set()
            try:
                await asyncio.Future()
            finally:
                stopped.append(index)

        task = asyncio.create_task(assemblyai_bridge._run_pair(lambda: pump(0), lambda: pump(1)))
        await asyncio.gather(*(event.wait() for event in started))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(stopped) == [0, 1]

    asyncio.run(scenario())


def test_failed_audio_pump_drains_its_peer_and_preserves_error():
    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def receiver():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def sender():
            await started.wait()
            raise OSError("disconnected")

        with pytest.raises(OSError, match="disconnected"):
            await assemblyai_bridge._run_pair(receiver, sender)
        assert stopped.is_set()

    asyncio.run(scenario())


class FakeProvider:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.sent = []
        self.result_sent = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, raw):
        self.sent.append(json.loads(raw))
        if self.sent[-1]["type"] == "tool.result":
            self.result_sent.set()

    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(0)
            yield json.dumps(event)
        if any(e.get("type") == "tool.call" for e in self.events):
            await asyncio.wait_for(self.result_sent.wait(), 2)
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


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def run_tool_result(response=None):
    gateway = FakeGateway(response)
    coordinator = AssemblyAIToolCoordinator(gateway)
    provider = FakeProvider([])
    dispatch = asyncio.create_task(coordinator.dispatch(provider))
    try:
        await coordinator.handle({"type": "session.ready", "session_id": "sess_123"})
        await coordinator.handle({"type": "tool.call", "call_id": "call_1", "name": "lookup", "arguments": {"x": 1}})
        await wait_until(lambda: coordinator._pending[0].result is not None)
        assert provider.sent == []
        await coordinator.handle({"type": "reply.done", "reply_id": "fc-call_1", "status": "completed"})
        await wait_until(lambda: provider.sent)
        return gateway, provider.sent
    finally:
        dispatch.cancel()
        await asyncio.gather(dispatch, return_exceptions=True)
        await coordinator.close()


def test_tool_result_waits_for_reply_done_and_uses_provider_session_and_call_ids():
    gateway, results = asyncio.run(run_tool_result())
    assert gateway.calls == [("lookup", {"x": 1}, "sess_123")]
    assert gateway.call_ids == ["call_1"]
    assert results[0]["call_id"] == "call_1"
    assert results[0]["is_error"] is False


def test_failed_tool_result_is_marked_as_an_error():
    _, results = asyncio.run(run_tool_result(VoiceToolResponse(ok=False, error={"code": "NOT_FOUND"})))
    assert results[0]["is_error"] is True
    assert json.loads(results[0]["result"])["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("interrupt", [False, True])
def test_slow_tool_after_reply_done_does_not_block_events_or_leak_to_a_new_turn(interrupt):
    started, release = threading.Event(), threading.Event()

    class SlowGateway(FakeGateway):
        def call(self, name, arguments, conversation_id=None, provider_call_id=None):
            started.set()
            assert release.wait(3)
            return super().call(name, arguments, conversation_id, provider_call_id)

    gateway = SlowGateway()

    async def scenario():
        coordinator = AssemblyAIToolCoordinator(gateway)
        provider = FakeProvider([])
        dispatcher = asyncio.create_task(coordinator.dispatch(provider))
        try:
            await coordinator.handle({"type": "session.ready", "session_id": "sess_slow"})
            await coordinator.handle({"type": "tool.call", "call_id": "1", "name": "lookup", "arguments": {}})
            await wait_until(started.is_set)
            # This is the boundary that previously waited for the thread and blocked barge-in.
            await asyncio.wait_for(coordinator.handle({"type": "reply.done", "reply_id": "fc-1", "status": "completed"}), .1)
            if interrupt:
                await asyncio.wait_for(coordinator.handle({"type": "input.speech.started"}), .1)
                await coordinator.handle({"type": "reply.done", "reply_id": "fc-1", "status": "interrupted"})
            release.set()
            await wait_until(lambda: not coordinator._monitors)
            if interrupt:
                await coordinator.handle({"type": "reply.started", "reply_id": "new-turn"})
                await coordinator.handle({"type": "reply.done", "reply_id": "new-turn", "status": "completed"})
                await asyncio.sleep(.01)
                assert provider.sent == []
            else:
                await wait_until(lambda: provider.sent)
                assert provider.sent[0]["call_id"] == "1"
        finally:
            release.set()
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
            await coordinator.close()
    asyncio.run(scenario())
    assert len(gateway.calls) == 1  # Started work completes even when speech is interrupted.


def test_timeout_keeps_started_work_in_order_and_reports_unknown_outcome(monkeypatch):
    monkeypatch.setattr(assemblyai_bridge, "TOOL_TIMEOUT_SECONDS", .03)
    release, started = threading.Event(), threading.Event()

    class SlowGateway(FakeGateway):
        def call(self, name, arguments, conversation_id=None, provider_call_id=None):
            if name == "first":
                started.set()
                assert release.wait(3)
            return super().call(name, arguments, conversation_id, provider_call_id)

    gateway = SlowGateway()

    async def scenario():
        coordinator = AssemblyAIToolCoordinator(gateway)
        provider = FakeProvider([])
        dispatcher = asyncio.create_task(coordinator.dispatch(provider))
        try:
            await coordinator.handle({"type": "session.ready", "session_id": "sess_timeout"})
            await coordinator.handle({"type": "tool.call", "call_id": "1", "name": "first", "arguments": {}})
            await wait_until(started.is_set)
            await coordinator.handle({"type": "reply.done", "reply_id": "fc-1", "status": "completed"})
            await wait_until(lambda: provider.sent)
            assert json.loads(provider.sent[0]["result"])["error"]["code"] == "TOOL_TIMEOUT"
            await coordinator.handle({"type": "tool.call", "call_id": "2", "name": "second", "arguments": {}})
            assert gateway.calls == []
            release.set()
            await wait_until(lambda: len(gateway.calls) == 2)
            assert [c[0] for c in gateway.calls] == ["first", "second"]
        finally:
            release.set()
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
            await coordinator.close()
    asyncio.run(scenario())


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
    assert provider.sent[1]["session"]["input"] == {
        "turn_detection": {"interrupt_response": False}
    }
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


def test_reconnect_holds_completed_results_until_matching_new_boundary():
    async def scenario():
        coordinator = AssemblyAIToolCoordinator(FakeGateway())
        provider = FakeProvider([])
        dispatch = asyncio.create_task(coordinator.dispatch(provider))
        try:
            await coordinator.handle({"type": "session.ready", "session_id": "sess_resume"})
            await coordinator.handle({"type": "tool.call", "call_id": "1", "name": "lookup", "arguments": {}})
            await wait_until(lambda: coordinator._pending[0].result is not None)
            coordinator.disconnected()
            await coordinator.handle({"type": "session.ready", "session_id": "sess_resume"})
            await coordinator.handle({"type": "reply.done", "reply_id": "fc-other", "status": "completed"})
            await asyncio.sleep(.01)
            assert provider.sent == []
            await coordinator.handle({"type": "reply.done", "reply_id": "fc-1", "status": "completed"})
            await wait_until(lambda: provider.sent)
            assert provider.sent[0]["call_id"] == "1"
            with pytest.raises(assemblyai_bridge.AssemblyAIResumeError):
                await coordinator.handle({"type": "session.ready", "session_id": "different_session"})
        finally:
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)
            await coordinator.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("finish", ["interrupt", "close", "expire"])
def test_queued_work_cannot_start_after_interruption_close_or_deadline(monkeypatch, finish):
    release, started = threading.Event(), threading.Event()
    if finish == "expire":
        monkeypatch.setattr(assemblyai_bridge, "TOOL_TIMEOUT_SECONDS", .02)

    class SlowGateway(FakeGateway):
        def call(self, name, arguments, conversation_id=None, provider_call_id=None):
            if name == "first":
                started.set()
                assert release.wait(3)
            return super().call(name, arguments, conversation_id, provider_call_id)

    gateway = SlowGateway()

    async def scenario():
        coordinator = AssemblyAIToolCoordinator(gateway)
        try:
            await coordinator.handle({"type": "session.ready", "session_id": "sess_queue"})
            for call_id, name in [("1", "first"), ("2", "second")]:
                await coordinator.handle({"type": "tool.call", "call_id": call_id, "name": name, "arguments": {}})
            await wait_until(started.is_set)
            if finish == "interrupt":
                await coordinator.handle({"type": "input.speech.started"})
            elif finish == "close":
                await coordinator.close()
            else:
                await wait_until(lambda: all(p.expired for p in coordinator._pending))
            release.set()
            await wait_until(lambda: not coordinator._executions)
            assert [c[0] for c in gateway.calls] == ["first"]
        finally:
            release.set()
            await coordinator.close()
    asyncio.run(scenario())


def test_twilio_bridge_maps_pcmu_audio_barge_in_and_tool_results(monkeypatch):
    provider = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_phone"},
            {"type": "session.updated"},
            {"type": "reply.audio", "data": "outbound-pcmu"},
            {"type": "input.speech.started"},
            {"type": "tool.call", "call_id": "call_1", "name": "lookup", "arguments": {"id": "1"}},
            {"type": "reply.done", "reply_id": "fc-call_1", "status": "completed"},
        ]
    )
    twilio = FakeClientSocket(
        [
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

    asyncio.run(assemblyai_bridge.bridge_twilio(twilio, settings, gateway, "CA123", "MZ123"))

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


def test_browser_bridge_resumes_after_a_transient_provider_session_error(monkeypatch):
    first = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_transient"},
            {"type": "session.updated"},
            {"type": "session.error", "code": "server_error", "message": "at capacity"},
        ]
    )
    resumed = FakeProvider(
        [
            {"type": "session.ready", "session_id": "sess_transient"},
            {"type": "transcript.agent", "text": "I am still here."},
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

    assert resumed.sent[0] == {"type": "session.resume", "session_id": "sess_transient"}
    assert client.sent[-1] == {"type": "transcript.agent", "text": "I am still here."}


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
        []
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

    asyncio.run(assemblyai_bridge.bridge_twilio(twilio, settings, FakeGateway(), "CA123", "MZ123"))

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
