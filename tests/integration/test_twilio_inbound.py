"""Inbound calls on our own Twilio number, bridged to AssemblyAI."""

import logging
from urllib.parse import parse_qs, urlencode, urlsplit
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
    token = parse_qs(parsed.query)["token"][0]
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
    assert page.status_code == 200 and "Pre-authorisation voice assistant" in page.text
    assert script.status_code == 200 and "input.audio" in script.text
    assert "must-not-be-in-html" not in page.text + script.text


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
