"""Inbound calls on our own Twilio number, handed to the selected hosted voice provider.

Twilio posts the incoming call here and we verify Twilio's signature. ElevenLabs calls retain their existing
register-call flow. AssemblyAI calls receive local TwiML containing a signed, short-lived media WebSocket URL;
the API bridge then proxies PCMU audio to the configured stored agent. Nothing here touches a case.

Security: the endpoint refuses to run unless it can check signatures, and it checks them against the public URL
Twilio was configured with. Logs carry the CallSid and the last four digits of each number, never tokens.
"""

import logging
from html import escape
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from preauth.domain.errors import DomainError
from preauth.infrastructure import twilio_signature
from preauth.infrastructure import assemblyai_media_token
from preauth.infrastructure.elevenlabs_register_call import (
    ElevenLabsRegisterCallClient,
    RegisterCallClient,
    RegisterCallError,
)
from preauth.infrastructure.settings import Settings, VoiceProvider

logger = logging.getLogger("preauth.voice.twilio")

INBOUND_PATH = "/api/v1/voice/twilio/inbound"
ASSEMBLYAI_TWILIO_PATH = "/api/v1/voice/assemblyai/twilio"

# Said to the caller when the agent cannot be reached. Twilio's own error would be a generic "application error".
FALLBACK_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?><Response>'
    "<Say>Sorry, the Sawt Assurance pre-authorisation line is unavailable right now. Please try again shortly."
    "</Say><Hangup/></Response>"
)


class TwilioChannelNotConfiguredError(DomainError):
    code = "CHANNEL_NOT_CONFIGURED"


class InboundCallInvalidError(DomainError):
    code = "TWILIO_CALL_INVALID"


def _tail(number: str) -> str:
    """Enough of a phone number to correlate with Twilio's logs, not enough to identify the caller."""
    digits = "".join(c for c in number if c.isdigit())
    return f"…{digits[-4:]}" if len(digits) >= 4 else "…"


@dataclass(frozen=True)
class InboundCallResult:
    twiml: str
    registered: bool


class TwilioInboundService:
    def __init__(
        self,
        *,
        auth_token: str | None,
        public_base_url: str | None,
        agent_id: str | None,
        client: RegisterCallClient | None,
        provider: VoiceProvider = VoiceProvider.ELEVENLABS,
        media_secret: str | None = None,
        assemblyai_api_key: str | None = None,
    ):
        self._auth_token = auth_token
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._url = f"{self._public_base_url}{INBOUND_PATH}" if self._public_base_url else None
        self._agent_id = agent_id
        self._client = client
        self._provider = provider
        self._media_secret = media_secret
        self._assemblyai_api_key = assemblyai_api_key

    @classmethod
    def from_settings(cls, settings: Settings) -> "TwilioInboundService":
        client = None
        if settings.voice_provider is VoiceProvider.ELEVENLABS and settings.elevenlabs_api_key:
            client = ElevenLabsRegisterCallClient(settings.elevenlabs_api_key)
        return cls(
            auth_token=settings.twilio_auth_token,
            public_base_url=settings.public_base_url,
            agent_id=(
                settings.assemblyai_phone_agent_id
                if settings.voice_provider is VoiceProvider.ASSEMBLYAI
                else settings.elevenlabs_agent_id
            ),
            client=client,
            provider=settings.voice_provider,
            media_secret=settings.assemblyai_media_secret,
            assemblyai_api_key=settings.assemblyai_api_key,
        )

    def missing_configuration(self) -> list[str]:
        common = [("TWILIO_AUTH_TOKEN", self._auth_token), ("PREAUTH_PUBLIC_BASE_URL", self._url)]
        provider = (
            [
                ("ASSEMBLYAI_API_KEY", self._assemblyai_api_key),
                ("PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID", self._agent_id),
                ("PREAUTH_ASSEMBLYAI_MEDIA_SECRET", self._media_secret),
            ]
            if self._provider is VoiceProvider.ASSEMBLYAI
            else [("ELEVENLABS_API_KEY", self._client), ("PREAUTH_ELEVENLABS_AGENT_ID", self._agent_id)]
        )
        return [name for name, value in common + provider if not value]

    def _assemblyai_twiml(self, call_sid: str) -> str:
        token = assemblyai_media_token.issue(call_sid, self._media_secret)
        public = urlsplit(self._public_base_url)
        scheme = "wss" if public.scheme == "https" else "ws"
        path = f"{public.path.rstrip('/')}{ASSEMBLYAI_TWILIO_PATH}"
        stream_url = urlunsplit((scheme, public.netloc, path, urlencode({"token": token}), ""))
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
            f'<Stream url="{escape(stream_url, quote=True)}"/></Connect></Response>'
        )

    def handle(self, raw_body: bytes, signature: str | None, query_string: str = "") -> InboundCallResult:
        missing = self.missing_configuration()
        if missing:
            raise TwilioChannelNotConfiguredError(
                "Inbound Twilio calls are disabled until configured", details={"missing": missing}
            )

        params = parse_qsl(raw_body.decode(errors="replace"), keep_blank_values=True)
        # Twilio signs the exact URL it called, including any query string configured on the number.
        url = f"{self._url}?{query_string}" if query_string else self._url
        twilio_signature.verify(url, params, signature, self._auth_token)

        form: dict[str, Any] = dict(params)
        call_sid = form.get("CallSid") or None
        from_number, to_number = (form.get("From") or "").strip(), (form.get("To") or "").strip()
        logger.info(
            "twilio_inbound_call_received",
            extra={"call_sid": call_sid, "from_tail": _tail(from_number), "to_tail": _tail(to_number)},
        )
        if not from_number or not to_number:
            raise InboundCallInvalidError(
                "Inbound call is missing From or To",
                details={"missing": [n for n, v in (("From", from_number), ("To", to_number)) if not v]},
            )

        if self._provider is VoiceProvider.ASSEMBLYAI:
            if not call_sid:
                raise InboundCallInvalidError(
                    "Inbound call is missing CallSid", details={"missing": ["CallSid"]}
                )
            logger.info("assemblyai_media_stream_issued", extra={"call_sid": call_sid})
            return InboundCallResult(twiml=self._assemblyai_twiml(call_sid), registered=True)

        try:
            twiml = self._client.register_call(
                {
                    "agent_id": self._agent_id,
                    "from_number": from_number,
                    "to_number": to_number,
                    "direction": "inbound",
                }
            )
        except RegisterCallError as e:
            logger.error(
                "elevenlabs_register_call_failed",
                extra={"call_sid": call_sid, "upstream_status": e.status, "reason": str(e)[:200]},
            )
            return InboundCallResult(twiml=FALLBACK_TWIML, registered=False)
        logger.info("elevenlabs_register_call_succeeded", extra={"call_sid": call_sid})
        return InboundCallResult(twiml=twiml, registered=True)
