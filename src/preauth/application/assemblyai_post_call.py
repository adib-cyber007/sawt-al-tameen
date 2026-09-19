"""AssemblyAI completed-session webhook and timeline-artifact ingestion.

The webhook is a notification, not the transcript itself. The handler verifies the raw delivery, fetches the
authoritative session, downloads its short-lived timeline artifact, normalizes it, and writes through the same
append-only call-record service used by ElevenLabs and local mode. Missing artifacts are a retryable condition:
AssemblyAI commonly emits ``session.completed`` before post-call artifacts are ready.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from preauth.application.voice_channel_service import PostCallOutcome, VoiceChannelService
from preauth.domain.errors import DomainError
from preauth.infrastructure import assemblyai_signature
from preauth.infrastructure.assemblyai_client import AssemblyAIClient, AssemblyAIError
from preauth.infrastructure.settings import Settings

logger = logging.getLogger("preauth.voice.assemblyai.post_call")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
WEBHOOK_UPSTREAM_TIMEOUT_SECS = 10


class AssemblyAIChannelNotConfiguredError(DomainError):
    code = "CHANNEL_NOT_CONFIGURED"


class AssemblyAIWebhookPayloadError(DomainError):
    code = "WEBHOOK_PAYLOAD_INVALID"


class AssemblyAIArtifactPendingError(DomainError):
    code = "WEBHOOK_ARTIFACT_PENDING"


class AssemblyAIProviderUnavailableError(DomainError):
    code = "VOICE_PROVIDER_UNAVAILABLE"


class AssemblyAIWebhookEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    event_id: str | None = None
    type: str | None = None
    event: str | None = None
    session_id: str | None = None
    timestamp: int | float | str | None = None
    created_at: int | float | str | None = None
    session: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def event_type(self) -> str | None:
        return self.type or self.event

    @property
    def resolved_event_id(self) -> str | None:
        return self.event_id or self.id

    @property
    def resolved_session_id(self) -> str | None:
        data_session = self.data.get("session")
        data_session_id = None
        if isinstance(data_session, dict):
            data_session_id = data_session.get("session_id") or data_session.get("id")
        value = (
            self.session_id
            or self.session.get("session_id")
            or self.session.get("id")
            or self.data.get("session_id")
            or self.data.get("id")
            or data_session_id
        )
        return value if isinstance(value, str) else None

    @property
    def resolved_timestamp(self) -> int | float | str | None:
        return self.timestamp if self.timestamp is not None else self.created_at


def _event_timestamp(value: int | float | str | None) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _duration(session: dict[str, Any]) -> int | None:
    for key in ("session_duration_seconds", "duration_seconds"):
        value = session.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    value = session.get("duration_ms")
    return int(value / 1000) if isinstance(value, (int, float)) and value >= 0 else None


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    value = call.get("arguments") or call.get("args")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except ValueError:
            return {}
    return {}


def normalize_timeline(timeline: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """Convert AssemblyAI's turn-oriented timeline into the existing role-oriented transcript."""
    turns = timeline.get("turns", [])
    if not isinstance(turns, list):
        raise AssemblyAIArtifactPendingError("AssemblyAI timeline is not ready")

    transcript: list[dict[str, Any]] = []
    interrupted = tool_calls = tool_errors = caller_turns = 0
    confidences: list[float] = []
    first_audio: list[int] = []
    summary: str | None = None
    for index, raw_turn in enumerate(turns):
        if not isinstance(raw_turn, dict):
            continue
        common = {"turn": index, "trigger": raw_turn.get("trigger"), "status": raw_turn.get("status")}
        user_text = raw_turn.get("user_transcript")
        if isinstance(user_text, str) and user_text.strip():
            caller_turns += 1
            transcript.append({"role": "caller", "message": user_text.strip(), **common})
        confidence = raw_turn.get("user_confidence")
        if isinstance(confidence, (int, float)):
            confidences.append(float(confidence))
        latency = raw_turn.get("time_to_first_audio_ms")
        if isinstance(latency, int):
            first_audio.append(latency)
        if raw_turn.get("status") == "interrupted":
            interrupted += 1

        calls = raw_turn.get("tool_calls", [])
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                tool_calls += 1
                is_error = bool(call.get("is_error") or call.get("timed_out"))
                tool_errors += int(is_error)
                name = call.get("name") or call.get("tool_name")
                arguments = _arguments(call)
                transcript.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "arguments": arguments,
                        "is_error": is_error,
                        "duration_ms": call.get("duration_ms"),
                        **common,
                    }
                )
                if name == "log_transcript" and isinstance(arguments.get("summary"), str):
                    summary = arguments["summary"].strip() or summary

        agent_text = raw_turn.get("agent_text")
        if isinstance(agent_text, str) and agent_text.strip():
            transcript.append({"role": "agent", "message": agent_text.strip(), **common})

    metrics: dict[str, Any] = {
        "turns": len(turns),
        "caller_turns": caller_turns,
        "interrupted_turns": interrupted,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
    }
    if confidences:
        metrics["average_user_confidence"] = sum(confidences) / len(confidences)
    if first_audio:
        metrics["average_time_to_first_audio_ms"] = int(sum(first_audio) / len(first_audio))
    return transcript, metrics, summary


class AssemblyAIPostCallService:
    def __init__(
        self,
        settings: Settings,
        voice: VoiceChannelService,
        client: AssemblyAIClient | None = None,
    ):
        self._settings = settings
        self._voice = voice
        self._client = client or (
            AssemblyAIClient(
                settings.assemblyai_api_key,
                api_base=settings.assemblyai_api_base,
                timeout_secs=WEBHOOK_UPSTREAM_TIMEOUT_SECS,
            )
            if settings.assemblyai_api_key
            else None
        )

    def missing_configuration(self) -> list[str]:
        return [
            name for name, value in (
                ("ASSEMBLYAI_API_KEY", self._client),
                ("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET", self._settings.assemblyai_webhook_secret),
            ) if not value
        ]

    def handle(self, raw_body: bytes, signature_header: str | None) -> PostCallOutcome:
        missing = self.missing_configuration()
        if missing:
            raise AssemblyAIChannelNotConfiguredError(
                "AssemblyAI post-call ingestion is disabled until configured", details={"missing": missing}
            )
        assemblyai_signature.verify(raw_body, signature_header, self._settings.assemblyai_webhook_secret)
        try:
            event = AssemblyAIWebhookEvent.model_validate(json.loads(raw_body))
        except (ValueError, ValidationError) as exc:
            raise AssemblyAIWebhookPayloadError(
                "Webhook body is not a valid AssemblyAI event", details={"reason": str(exc)[:500]}
            ) from exc
        if event.event_type != "session.completed":
            logger.info("assemblyai_post_call_event_ignored", extra={"event_type": event.event_type})
            return PostCallOutcome(accepted=False, detail=f"Event type {event.event_type!r} is not stored")
        session_id = event.resolved_session_id
        if not session_id or not _SESSION_ID.fullmatch(session_id):
            raise AssemblyAIWebhookPayloadError("AssemblyAI event has no valid session_id")
        return self.ingest_session(
            session_id,
            event_id=event.resolved_event_id,
            event_timestamp=_event_timestamp(event.resolved_timestamp),
        )

    def ingest_session(
        self,
        session_id: str,
        *,
        event_id: str | None = None,
        event_timestamp: int | None = None,
    ) -> PostCallOutcome:
        if not self._client:
            raise AssemblyAIChannelNotConfiguredError("ASSEMBLYAI_API_KEY is not set")
        try:
            session = self._client.get_session(session_id)
            returned_id = session.get("id") or session.get("session_id")
            if returned_id is not None and returned_id != session_id:
                raise AssemblyAIProviderUnavailableError("AssemblyAI returned a different session")
            artifacts = session.get("artifacts", [])
            if not isinstance(artifacts, list):
                raise AssemblyAIArtifactPendingError("AssemblyAI session artifacts are not ready")
            timeline_artifact = next(
                (
                    artifact for artifact in artifacts
                    if isinstance(artifact, dict)
                    and (artifact.get("type") or artifact.get("artifact_type")) == "timeline"
                    and isinstance(artifact.get("url"), str)
                ),
                None,
            )
            if timeline_artifact is None:
                raise AssemblyAIArtifactPendingError("AssemblyAI timeline artifact is not ready")
            timeline = self._client.download_json(timeline_artifact["url"])
        except (AssemblyAIArtifactPendingError, AssemblyAIProviderUnavailableError):
            raise
        except AssemblyAIError as exc:
            logger.warning(
                "assemblyai_session_fetch_failed", extra={"session_id": session_id, "upstream_status": exc.status}
            )
            raise AssemblyAIProviderUnavailableError(
                "AssemblyAI session artifacts could not be retrieved",
                details={"upstream_status": exc.status},
            ) from None

        transcript, metrics, inferred_summary = normalize_timeline(timeline)
        status = session.get("status") if isinstance(session.get("status"), str) else "completed"
        agent_id = session.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            agent_id = "assemblyai-agent"
        explicit_summary = session.get("summary")
        summary = (
            explicit_summary.strip()
            if isinstance(explicit_summary, str) and explicit_summary.strip()
            else inferred_summary
        )
        artifact_types = [
            a.get("type") or a.get("artifact_type")
            for a in session.get("artifacts", [])
            if isinstance(a, dict)
        ]
        metadata = {
            "event_id": event_id,
            "started_at": session.get("started_at") or session.get("created_at"),
            "ended_at": session.get("ended_at"),
            "close_reason": session.get("public_close_reason") or session.get("close_reason"),
            "artifact_types": artifact_types,
        }
        analysis = {"timeline_metrics": metrics}
        logger.info(
            "assemblyai_session_artifacts_ready",
            extra={"session_id": session_id, "turns": metrics["turns"]},
        )
        return self._voice.record_assemblyai_call(
            session_id=session_id,
            agent_id=agent_id,
            status=status,
            transcript=transcript,
            summary=summary,
            call_successful="success" if status in {"complete", "completed"} else "unknown",
            call_duration_secs=_duration(session),
            analysis=analysis,
            metadata=metadata,
            event_timestamp=event_timestamp,
        )
