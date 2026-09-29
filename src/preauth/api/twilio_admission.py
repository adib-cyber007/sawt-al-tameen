"""Authenticate the upgrade and bounded start handshake before opening a voice session."""

import asyncio
import json
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect

from preauth.application.twilio_inbound_service import media_stream_url
from preauth.infrastructure import assemblyai_media_token, twilio_signature
from preauth.infrastructure.settings import Settings

START_TIMEOUT_SECONDS = 5
MAX_START_FRAME_BYTES = 4096


@dataclass(frozen=True)
class TwilioStart:
    call_sid: str
    stream_sid: str
    token: str


def verify_upgrade(websocket: WebSocket, settings: Settings) -> None:
    if websocket.url.query:
        raise ValueError("Query strings are not supported")
    url = media_stream_url(settings.public_base_url)
    signature = websocket.headers.get("x-twilio-signature")
    # Twilio documents a trailing-slash variant for Voice WSS handshakes. Both candidates
    # use the configured public host/path, never untrusted Host or forwarded headers.
    for candidate in (url, url + "/"):
        try:
            twilio_signature.verify(candidate, (), signature, settings.twilio_auth_token)
            return
        except twilio_signature.TwilioSignatureError:
            pass
    raise ValueError("Invalid upgrade signature")


async def read_start(websocket: WebSocket, settings: Settings) -> TwilioStart:
    async with asyncio.timeout(START_TIMEOUT_SECONDS):
        # At most one connected frame followed by start. No pre-auth audio buffering.
        for index in range(2):
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            raw = message.get("text")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_START_FRAME_BYTES:
                raise ValueError("Invalid start frame")
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("Invalid start message")
            if index == 0 and event.get("event") == "connected":
                if event.get("protocol") != "Call" or event.get("version") != "1.0.0":
                    raise ValueError("Unsupported stream protocol")
                continue
            start = event.get("start")
            if event.get("event") != "start" or not isinstance(start, dict):
                raise ValueError("Expected start")
            parameters = start.get("customParameters")
            token = parameters.get("token") if isinstance(parameters, dict) else None
            if not isinstance(token, str) or len(token) > 500:
                raise ValueError("Missing media token")
            verified = assemblyai_media_token.verify(token, settings.assemblyai_media_secret)
            call_sid, stream_sid = start.get("callSid"), start.get("streamSid")
            if call_sid != verified.call_sid:
                raise ValueError("Call identity mismatch")
            if not isinstance(stream_sid, str) or not stream_sid or len(stream_sid) > 100:
                raise ValueError("Missing stream identity")
            if event.get("streamSid") != stream_sid:
                raise ValueError("Stream identity mismatch")
            if start.get("mediaFormat") != {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}:
                raise ValueError("Unsupported media format")
            return TwilioStart(call_sid, stream_sid, token)
    raise ValueError("Missing start")
