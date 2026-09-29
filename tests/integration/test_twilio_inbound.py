"""Inbound calls on our own Twilio number, bridged to AssemblyAI."""

import logging
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from preauth.api.app import create_app
from preauth.application.twilio_inbound_service import INBOUND_PATH, TwilioInboundService
from preauth.infrastructure import assemblyai_media_token, twilio_signature
from preauth.infrastructure.settings import Settings, VoiceProvider

AUTH_TOKEN = "test-twilio-auth-token"
PUBLIC = "https://sawt-al-tameen.ngrok-free.app"
AGENT = "agent_phone"
CALL = {"CallSid": "CA1234567890ABCDE", "From": "+971501234567", "To": "+14155550123", "Direction": "inbound"}


@pytest.fixture
def client(services):
    app = create_app(services, Settings())
    app.state.twilio_inbound = TwilioInboundService(
        auth_token=AUTH_TOKEN,
        public_base_url=PUBLIC,
        agent_id=AGENT,
        media_secret="media-secret",
        assemblyai_api_key="assemblyai-test-key",
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def post(client, form: dict, *, token: str = AUTH_TOKEN, signature: str | None = None):
    """Sign exactly as Twilio does: over the public URL it was configured with, not the test server's."""
    signed = signature if signature is not None else twilio_signature.sign(PUBLIC + INBOUND_PATH, form.items(), token)
    return client.post(
        INBOUND_PATH,
        content=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signed},
    )


@pytest.mark.parametrize("missing", ["From", "To"])
def test_a_call_missing_from_or_to_is_rejected(client, missing):
    form = {k: v for k, v in CALL.items() if k != missing}
    response = post(client, form)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TWILIO_CALL_INVALID"
    assert response.json()["error"]["details"]["missing"] == [missing]


@pytest.mark.parametrize(
    "signature",
    ["", "not-a-signature", twilio_signature.sign(PUBLIC + INBOUND_PATH, CALL.items(), "some-other-token")],
    ids=["missing", "garbage", "wrong-token"],
)
def test_an_unsigned_or_forged_request_is_rejected(client, signature):
    response = post(client, CALL, signature=signature)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TWILIO_SIGNATURE_INVALID"


def test_a_tampered_parameter_breaks_the_signature(client):
    signed = twilio_signature.sign(PUBLIC + INBOUND_PATH, CALL.items(), AUTH_TOKEN)
    tampered = {**CALL, "From": "+15550000000"}
    assert post(client, tampered, signature=signed).status_code == 401


def test_the_signature_is_checked_against_the_public_url_not_the_tunnel_address(client):
    """Behind ngrok/Cloudflare the request arrives on 127.0.0.1, but Twilio signed the public URL."""
    local = twilio_signature.sign("http://testserver" + INBOUND_PATH, CALL.items(), AUTH_TOKEN)
    assert post(client, CALL, signature=local).status_code == 401
    assert post(client, CALL).status_code == 200


def test_the_endpoint_is_disabled_until_fully_configured(services):
    with TestClient(create_app(services, Settings()), raise_server_exceptions=False) as client:
        response = post(client, CALL)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CHANNEL_NOT_CONFIGURED"
    assert set(response.json()["error"]["details"]["missing"]) == {
        "TWILIO_AUTH_TOKEN",
        "PREAUTH_PUBLIC_BASE_URL",
        "ASSEMBLYAI_API_KEY",
        "PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID",
        "PREAUTH_ASSEMBLYAI_MEDIA_SECRET",
    }


def test_logs_carry_the_call_sid_but_no_secret_or_full_number(client, caplog):
    with caplog.at_level(logging.DEBUG):
        post(client, CALL)
    events = {r.getMessage(): r for r in caplog.records if r.name == "preauth.voice.twilio"}
    assert events["twilio_inbound_call_received"].call_sid == "CA1234567890ABCDE"
    assert events["twilio_inbound_call_received"].from_tail == "…4567"
    assert "assemblyai_media_stream_issued" in events
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (AUTH_TOKEN, "+971501234567", "+14155550123"):
        assert secret not in text


def test_settings_keep_secrets_out_of_their_repr():
    settings = Settings(
        twilio_auth_token="tok-secret",
        assemblyai_api_key="aai-secret",
        assemblyai_media_secret="media-secret",
    )
    assert all(secret not in repr(settings) for secret in ("tok-secret", "aai-secret", "media-secret"))


def test_assemblyai_call_returns_a_signed_local_media_stream(services):
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        twilio_auth_token=AUTH_TOKEN,
        public_base_url=PUBLIC,
        assemblyai_api_key="assemblyai-test-key",
        assemblyai_phone_agent_id="agent_phone",
        assemblyai_media_secret="media-secret",
    )
    with TestClient(create_app(services, settings)) as client:
        response = post(client, CALL)

    assert response.status_code == 200
    stream = ElementTree.fromstring(response.text).find("./Connect/Stream")
    assert stream is not None
    parsed = urlsplit(stream.attrib["url"])
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "wss", "sawt-al-tameen.ngrok-free.app", "/api/v1/voice/assemblyai/twilio"
    )
    assert parsed.query == ""
    token = stream.find("Parameter").attrib["value"]
    assert stream.find("Parameter").attrib["name"] == "token"
    assert assemblyai_media_token.verify(token, "media-secret").call_sid == CALL["CallSid"]


def test_assemblyai_inbound_lists_its_own_missing_configuration(services):
    settings = Settings(voice_provider=VoiceProvider.ASSEMBLYAI)
    with TestClient(create_app(services, settings), raise_server_exceptions=False) as client:
        response = post(client, CALL)
    assert response.status_code == 503
    assert set(response.json()["error"]["details"]["missing"]) == {
        "TWILIO_AUTH_TOKEN",
        "PREAUTH_PUBLIC_BASE_URL",
        "ASSEMBLYAI_API_KEY",
        "PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID",
        "PREAUTH_ASSEMBLYAI_MEDIA_SECRET",
    }


def test_assemblyai_browser_console_is_served_without_exposing_the_api_key(services):
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="must-not-be-in-html",
        assemblyai_browser_agent_id="agent_browser",
    )
    with TestClient(create_app(services, settings)) as client:
        page = client.get("/voice")
        script = client.get("/voice/assets/app.js")
        worklet = client.get("/voice/assets/pcm-capture.js")
    assert page.status_code == 200 and "Pre-authorisation voice assistant" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert script.status_code == 200 and "input.audio" in script.text
    assert 'new AudioContext({ latencyHint: "playback" })' in script.text
    assert "targetSampleRate: 24000" in script.text
    assert "noiseSuppression: true" in script.text
    assert worklet.status_code == 200 and "this.ratio" in worklet.text
    assert "must-not-be-in-html" not in page.text + script.text + worklet.text


def test_assemblyai_twilio_socket_rejects_an_invalid_media_token_before_upstream_connect(services):
    settings = Settings(
        voice_provider=VoiceProvider.ASSEMBLYAI,
        assemblyai_api_key="test-key",
        assemblyai_phone_agent_id="agent_phone",
        assemblyai_media_secret="media-secret",
    )
    with TestClient(create_app(services, settings)) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/voice/assemblyai/twilio?token=forged"):
                pass
    assert exc.value.code == 1008


def test_signing_matches_twilios_official_request_validator():
    """Vector produced by twilio.request_validator.RequestValidator (twilio-python), not by this code."""
    params = {
        "CallSid": "CA1234567890ABCDE", "From": "+971501234567", "To": "+14155550123", "AccountSid": "AC00",
        "Direction": "inbound", "CallerName": "A & B, Clinic",
    }
    url = "https://sawt-al-tameen.ngrok-free.app/api/v1/voice/twilio/inbound"
    assert twilio_signature.sign(url, params.items(), "test-auth-token") == "w4A5Fs7SdJOFsBMQfm5s/Ja/krg="


SOCKET_PATH = "/api/v1/voice/assemblyai/twilio"
SOCKET_URL = PUBLIC.replace("https://", "wss://") + SOCKET_PATH


def phone_settings():
    return Settings(voice_provider=VoiceProvider.ASSEMBLYAI, twilio_auth_token=AUTH_TOKEN,
                    public_base_url=PUBLIC, assemblyai_api_key="test-key",
                    assemblyai_phone_agent_id=AGENT, assemblyai_media_secret="media-secret")


def start_event(token=None):
    return {"event": "start", "streamSid": "MZ123", "start": {
        "callSid": CALL["CallSid"], "streamSid": "MZ123",
        "customParameters": {"token": token or assemblyai_media_token.issue(CALL["CallSid"], "media-secret")},
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
    }}


@pytest.fixture
def admitted_calls(monkeypatch):
    from preauth.api.routes import assemblyai
    calls = []

    async def bridge(socket, settings, gateway, call_sid, stream_sid):
        calls.append((call_sid, stream_sid))
        await socket.close(code=1000)

    monkeypatch.setattr(assemblyai, "bridge_twilio", bridge)
    return calls


@pytest.mark.parametrize("signature", [None, "forged", twilio_signature.sign("wss://evil.example" + SOCKET_PATH, (), AUTH_TOKEN)])
def test_upgrade_rejects_missing_forged_or_wrong_host_signature(services, admitted_calls, signature):
    headers = {"x-twilio-signature": signature} if signature else {}
    with TestClient(create_app(services, phone_settings())) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(SOCKET_PATH, headers=headers):
                pass
    assert exc.value.code == 1008 and admitted_calls == []


@pytest.mark.parametrize("fault", ["expired", "forged", "call", "stream", "missing", "format", "shape", "oversize", "binary", "media", "flood"])
def test_start_rejected_before_opening_upstream(services, admitted_calls, fault):
    event = start_event()
    if fault == "expired":
        event = start_event(assemblyai_media_token.issue(CALL["CallSid"], "media-secret", now=1))
    elif fault == "forged":
        event = start_event("forged")
    elif fault == "call":
        event["start"]["callSid"] = "CAother"
    elif fault == "stream":
        event["streamSid"] = "MZother"
    elif fault == "missing":
        event["start"]["customParameters"] = {}
    elif fault == "format":
        event["start"]["mediaFormat"]["sampleRate"] = 24000
    elif fault == "shape":
        event = []
    elif fault == "media":
        event = {"event": "media", "media": {"payload": "audio-before-auth"}}
    elif fault == "flood":
        event = {"event": "connected", "protocol": "Call", "version": "1.0.0"}
    with TestClient(create_app(services, phone_settings())) as client:
        with client.websocket_connect(SOCKET_PATH, headers={"x-twilio-signature": twilio_signature.sign(SOCKET_URL, (), AUTH_TOKEN)}) as socket:
            if fault == "oversize":
                socket.send_text("x" * 4097)
            elif fault == "binary":
                socket.send_bytes(b"binary")
            else:
                socket.send_json(event)
                if fault == "flood":
                    socket.send_json(event)
            assert socket.receive()["code"] == 1008
    assert admitted_calls == []


def test_missing_start_times_out_without_upstream(services, admitted_calls, monkeypatch):
    from preauth.api import twilio_admission
    monkeypatch.setattr(twilio_admission, "START_TIMEOUT_SECONDS", .02)
    with TestClient(create_app(services, phone_settings())) as client:
        with client.websocket_connect(SOCKET_PATH, headers={"x-twilio-signature": twilio_signature.sign(SOCKET_URL, (), AUTH_TOKEN)}) as socket:
            assert socket.receive()["code"] == 1008
    assert admitted_calls == []


def test_valid_start_is_admitted_once_even_across_app_instances(services, admitted_calls):
    event = start_event()
    headers = {"x-twilio-signature": twilio_signature.sign(SOCKET_URL, (), AUTH_TOKEN)}
    for expected in (1000, 1008):
        with TestClient(create_app(services, phone_settings())) as client:
            with client.websocket_connect(SOCKET_PATH, headers=headers) as socket:
                socket.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
                socket.send_json(event)
                assert socket.receive()["code"] == expected
    assert admitted_calls == [(CALL["CallSid"], "MZ123")]
