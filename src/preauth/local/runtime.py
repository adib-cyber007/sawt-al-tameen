"""The local channel's composition root.

One object owns the local model, the speech adapters, the session store and the gateway onto the existing agent
tools. Everything that is specific to running locally is reachable from here, which is what keeps the rest of the
codebase free of ``if local:`` branching: the API layer either has a ``LocalRuntime`` or it does not.
"""

import base64
import logging
from dataclasses import dataclass
from typing import Any

from preauth.agent_tools.toolbox import AgentToolbox
from preauth.agent_tools.voice_gateway import LOCAL_AGENT_ACTOR, VoiceToolGateway
from preauth.application.services import ApplicationServices
from preauth.application.voice_channel_service import bind_voice_context
from preauth.local.agent import LocalAgent
from preauth.local.config import LocalSettings
from preauth.local.errors import ConversationClosedError, LocalModeError
from preauth.local.llm import ChatModel, build_chat_model
from preauth.local.session import LocalSession, SessionStore
from preauth.local.speech import (
    Synthesizer,
    Transcriber,
    build_synthesizer,
    build_transcriber,
)
from preauth.infrastructure.clock import Clock, SystemClock

logger = logging.getLogger("preauth.local.runtime")

AGENT_ID = "sawt-assurance-local-agent"


@dataclass
class LocalRuntime:
    services: ApplicationServices
    settings: LocalSettings
    agent: LocalAgent
    sessions: SessionStore
    transcriber: Transcriber
    synthesizer: Synthesizer
    clock: Clock

    @classmethod
    def build(
        cls,
        services: ApplicationServices,
        settings: LocalSettings | None = None,
        *,
        clock: Clock | None = None,
        model: ChatModel | None = None,
        transcriber: Transcriber | None = None,
        synthesizer: Synthesizer | None = None,
    ) -> "LocalRuntime":
        settings = settings or LocalSettings.from_env()
        toolbox = AgentToolbox(services)
        # A VOICE_AGENT actor: the review API rejects it, exactly as it rejects the hosted agent.
        gateway = VoiceToolGateway(services, toolbox, LOCAL_AGENT_ACTOR)
        return cls(
            services=services,
            settings=settings,
            agent=LocalAgent(model or build_chat_model(settings), gateway, toolbox, settings),
            sessions=SessionStore(),
            transcriber=transcriber or build_transcriber(settings),
            synthesizer=synthesizer or build_synthesizer(settings),
            clock=clock or SystemClock(),
        )

    # ------------------------------------------------------------------ conversation

    def _bind(self, session: LocalSession) -> LocalSession:
        """Put the conversation and the acting agent into the logging context for the rest of this call."""
        bind_voice_context(LOCAL_AGENT_ACTOR, session.conversation_id)
        return session

    def _open(self, conversation_id: str) -> LocalSession:
        """A session that can still take a turn.

        Once a call is finished its transcript is on record, and call records are immutable. A further turn could
        never be added to it, so it must be refused rather than handled and silently lost.
        """
        session = self._bind(self.sessions.get(conversation_id))
        if session.finished_at is not None:
            raise ConversationClosedError(
                "This call has ended and its transcript is stored; start a new call",
                details={"conversation_id": conversation_id, "call_record_id": session.call_record_id},
            )
        return session

    def start(self) -> dict[str, Any]:
        session = self._bind(self.sessions.create(self.clock.now()))
        turn = self.agent.greet(session, self.clock.now())
        return self._reply(session, turn.reply, turn.tool_calls, turn.state)

    def say(self, conversation_id: str, text: str) -> dict[str, Any]:
        session = self._open(conversation_id)
        turn = self.agent.respond(session, text, self.clock.now())
        payload = self._reply(session, turn.reply, turn.tool_calls, turn.state)
        payload["heard"] = text
        payload["decision_language_blocked"] = turn.decision_language_blocked
        return payload

    def listen(self, conversation_id: str, audio: bytes) -> dict[str, Any]:
        session = self._open(conversation_id)
        # Local voice support is intentionally English-only, matching the hosted AssemblyAI agent.
        transcription = self.transcriber.transcribe(audio, "en")
        logger.info(
            "stt_completed",
            extra={
                "language": transcription.language,
                "duration_seconds": transcription.duration_seconds,
                "characters": len(transcription.text),
            },
        )
        payload = self.say(conversation_id, transcription.text)
        payload["heard_language"] = transcription.language
        return payload

    def finish(self, conversation_id: str) -> dict[str, Any]:
        """End the call and store its transcript, which is what unblocks human sign-off on any case it touched."""
        session = self._bind(self.sessions.get(conversation_id))
        if session.finished_at is None:
            session.finished_at = self.clock.now()
        outcome = self.services.voice.record_local_call(
            conversation_id=session.conversation_id,
            agent_id=AGENT_ID,
            transcript=session.transcript(),
            summary=self._summary(session),
            call_duration_secs=int((session.finished_at - session.started_at).total_seconds()),
            analysis={
                "channel": "local",
                "tool_calls": session.tool_calls,
                "recommendation_outcome": session.recommendation_outcome,
                "escalation_rule_ids": session.escalation_rule_ids,
                "case_reference": session.case_reference,
            },
            metadata={"agent_model": self.agent.model.name, "transcriber": self.transcriber.name},
        )
        session.call_record_id = outcome.call_record_id
        return {
            "conversation_id": session.conversation_id,
            "call_record_id": outcome.call_record_id,
            "linked_case_ids": outcome.linked_case_ids,
            "detail": outcome.detail,
            "transcript": session.transcript(),
            "state": session.state(),
        }

    def state(self, conversation_id: str) -> dict[str, Any]:
        session = self._bind(self.sessions.get(conversation_id))
        return {"state": session.state(), "transcript": session.transcript()}

    def capabilities(self) -> dict[str, Any]:
        return {
            "model": self.agent.model.name,
            "transcriber": self.transcriber.name,
            "synthesizer": self.synthesizer.name,
            "speech_input": self.transcriber.enabled,
            "speech_output": self.synthesizer.enabled,
        }

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _summary(session: LocalSession) -> str:
        parts = [f"Local pre-authorisation call, {len(session.turns)} turns."]
        if session.case_reference:
            parts.append(f"Case {session.case_reference} ({session.recommendation_outcome or 'no recommendation'}).")
        if session.escalation_rule_ids:
            parts.append("Escalated under " + ", ".join(session.escalation_rule_ids) + ".")
        parts.append("No decision was issued on the call; sign-off remains with a human reviewer.")
        return " ".join(parts)

    def _reply(
        self, session: LocalSession, text: str, tool_calls: tuple[dict[str, Any], ...], state: dict[str, Any]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "conversation_id": session.conversation_id,
            "reply": text,
            "tool_calls": list(tool_calls),
            "state": state,
            "audio": None,
            "audio_media_type": None,
            "speech_error": None,
        }
        if not self.synthesizer.enabled:
            return payload
        try:
            speech = self.synthesizer.synthesize(text)
        except LocalModeError as exc:
            # A missing voice must not cost the caller the answer; the text reply still stands.
            logger.warning("tts_failed", extra={"error": exc.code})
            payload["speech_error"] = {"code": exc.code, "message": exc.message, "details": exc.details}
            return payload
        logger.info("tts_completed", extra={"bytes": len(speech.audio)})
        payload["audio"] = base64.b64encode(speech.audio).decode()
        payload["audio_media_type"] = speech.media_type
        return payload
