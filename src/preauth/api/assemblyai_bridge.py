"""Server-owned WebSocket bridge for AssemblyAI browser and Twilio voice sessions.

Keeping this bridge server-side prevents the AssemblyAI API key and business-tool credentials from reaching a
browser. It also binds every function call to AssemblyAI's authoritative session id, which lets the application
enforce its transcript-before-signoff invariant after the call.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from preauth.agent_tools.assemblyai import all_function_tool_configs
from preauth.agent_tools.voice_gateway import VoiceToolGateway
from preauth.infrastructure import assemblyai_media_token
from preauth.infrastructure.settings import Settings

logger = logging.getLogger("preauth.voice.assemblyai.bridge")
RESUME_WINDOW_SECONDS = 30
MAX_RESUME_ATTEMPTS = 3
_UPSTREAM_ERRORS = (ConnectionClosed, WebSocketException, TimeoutError, OSError)


class AssemblyAIResumeError(RuntimeError):
    """The provider session could not be resumed without losing correlation or conversation context."""


class AssemblyAIConfigurationError(RuntimeError):
    """The provider rejected the client-side tool configuration for a new session."""


class AssemblyAIToolCoordinator:
    """Executes function tools and releases results only at AssemblyAI's safe reply boundary."""

    def __init__(self, gateway: VoiceToolGateway):
        self._gateway = gateway
        self.session_id: str | None = None
        self.ready_count = 0
        self._pending: list[dict[str, Any]] = []

    async def handle(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = event.get("type")
        if event_type == "session.ready":
            session_id = event.get("session_id")
            if isinstance(session_id, str) and session_id:
                self.session_id = session_id
                self.ready_count += 1
            return []

        if event_type == "tool.call":
            call_id, name, arguments = event.get("call_id"), event.get("name"), event.get("arguments")
            if not isinstance(call_id, str) or not isinstance(name, str) or not isinstance(arguments, dict):
                logger.warning("assemblyai_tool_call_malformed")
                return []
            try:
                response = await asyncio.to_thread(self._gateway.call, name, arguments, self.session_id)
                value = response.model_dump(mode="json")
                is_error = not response.ok
            except Exception:
                # An unexpected adapter failure must be useful to the model without exposing internals or secrets.
                logger.exception("assemblyai_tool_call_unexpected_failure", extra={"tool": name})
                value = {
                    "ok": False,
                    "error": {"code": "TOOL_EXECUTION_FAILED", "message": "The tool could not be completed."},
                    "guidance": "Apologise briefly and offer a human callback.",
                }
                is_error = True
            self._pending.append(
                {
                    "type": "tool.result",
                    "call_id": call_id,
                    "result": json.dumps(value, separators=(",", ":")),
                    "is_error": is_error,
                }
            )
            return []

        if event_type == "reply.done":
            if event.get("status") == "interrupted":
                self._pending.clear()
                return []
            ready, self._pending = self._pending, []
            return ready
        return []


def twilio_call_sid(settings: Settings, token: str) -> str | None:
    if not settings.assemblyai_media_secret:
        return None
    try:
        return assemblyai_media_token.verify(token, settings.assemblyai_media_secret).call_sid
    except assemblyai_media_token.MediaTokenInvalidError:
        return None


async def _send_provider(provider: Any, payload: dict[str, Any]) -> None:
    await provider.send(json.dumps(payload, separators=(",", ":")))


def _safe_browser_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Do not expose tool arguments or the stored agent's expanded configuration to the browser."""
    event_type = event.get("type")
    if event_type in {"tool.call", "session.updated"}:
        return None
    if event_type == "session.ready":
        return {"type": "session.ready", "session_id": event.get("session_id")}
    return event


async def _run_pair(left: Callable[[], Awaitable[None]], right: Callable[[], Awaitable[None]]) -> None:
    tasks = {asyncio.create_task(left()), asyncio.create_task(right())}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        exc = task.exception()
        if exc:
            raise exc


async def _run_resumable(
    coordinator: AssemblyAIToolCoordinator,
    connect_once: Callable[[str | None], Awaitable[None]],
) -> None:
    """Reconnect abnormal provider drops within AssemblyAI's documented 30-second resume window."""
    deadline: float | None = None
    attempts = 0
    while True:
        ready_before = coordinator.ready_count
        try:
            await connect_once(coordinator.session_id)
            return
        except _UPSTREAM_ERRORS as exc:
            # A connection that reached session.ready starts a fresh recovery window, including after a
            # successful resume followed by a later independent network drop.
            now = time.monotonic()
            if coordinator.ready_count > ready_before:
                deadline = now + RESUME_WINDOW_SECONDS
                attempts = 0
            if not coordinator.session_id or deadline is None:
                raise
            attempts += 1
            if attempts > MAX_RESUME_ATTEMPTS or now >= deadline:
                raise AssemblyAIResumeError("AssemblyAI session resume window was exhausted") from exc
            logger.warning(
                "assemblyai_session_reconnecting",
                extra={
                    "session_id": coordinator.session_id,
                    "attempt": attempts,
                    "close_code": getattr(exc, "code", None),
                },
            )
            await asyncio.sleep(min(0.25 * (2 ** (attempts - 1)), max(0.0, deadline - now)))


async def bridge_browser(websocket: WebSocket, settings: Settings, gateway: VoiceToolGateway) -> None:
    coordinator = AssemblyAIToolCoordinator(gateway)
    client_active = True

    async def connect_once(resume_session_id: str | None) -> None:
        nonlocal client_active
        ready = asyncio.Event()
        async with connect(
            settings.assemblyai_ws_url,
            additional_headers={"Authorization": f"Bearer {settings.assemblyai_api_key}"},
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        ) as provider:
            if resume_session_id:
                await _send_provider(
                    provider, {"type": "session.resume", "session_id": resume_session_id}
                )
            else:
                await _send_provider(
                    provider,
                    {"type": "session.update", "session": {"agent_id": settings.assemblyai_browser_agent_id}},
                )

            async def from_browser() -> None:
                nonlocal client_active
                try:
                    while True:
                        event = await websocket.receive_json()
                        event_type = event.get("type") if isinstance(event, dict) else None
                        if event_type == "input.audio" and isinstance(event.get("audio"), str):
                            await ready.wait()
                            await _send_provider(provider, {"type": "input.audio", "audio": event["audio"]})
                        elif event_type == "session.end":
                            client_active = False
                            await _send_provider(provider, {"type": "session.end"})
                            return
                except WebSocketDisconnect:
                    client_active = False
                    try:
                        await _send_provider(provider, {"type": "session.end"})
                    except _UPSTREAM_ERRORS:
                        pass

            async def from_provider() -> None:
                async for raw in provider:
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "session.ready":
                        logger.info(
                            "assemblyai_browser_session_ready", extra={"session_id": event.get("session_id")}
                        )
                        if resume_session_id:
                            ready.set()
                        else:
                            await _send_provider(
                                provider,
                                {"type": "session.update", "session": {"tools": all_function_tool_configs()}},
                            )
                    elif event_type == "session.updated" and not resume_session_id:
                        ready.set()
                    if event_type == "session.error":
                        if event.get("code") in {"session_not_found", "session_forbidden", "session_expired"}:
                            raise AssemblyAIResumeError("AssemblyAI refused to resume the session")
                        if not ready.is_set():
                            raise AssemblyAIConfigurationError("AssemblyAI rejected the session tool configuration")
                    for result in await coordinator.handle(event):
                        await _send_provider(provider, result)
                    safe = _safe_browser_event(event)
                    if safe is not None:
                        await websocket.send_json(safe)

            await _run_pair(from_browser, from_provider)

    await _run_resumable(coordinator, connect_once)
    if not client_active:
        return


async def bridge_twilio(
    websocket: WebSocket, settings: Settings, gateway: VoiceToolGateway, expected_call_sid: str
) -> None:
    coordinator = AssemblyAIToolCoordinator(gateway)
    stream_sid: str | None = None
    client_active = True

    async def connect_once(resume_session_id: str | None) -> None:
        nonlocal stream_sid, client_active
        provider_ready, twilio_ready = asyncio.Event(), asyncio.Event()
        if stream_sid:
            twilio_ready.set()
        async with connect(
            settings.assemblyai_ws_url,
            additional_headers={"Authorization": f"Bearer {settings.assemblyai_api_key}"},
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        ) as provider:
            if resume_session_id:
                await _send_provider(
                    provider, {"type": "session.resume", "session_id": resume_session_id}
                )
            else:
                await _send_provider(
                    provider,
                    {"type": "session.update", "session": {"agent_id": settings.assemblyai_phone_agent_id}},
                )

            async def from_twilio() -> None:
                nonlocal stream_sid, client_active
                try:
                    while True:
                        event = await websocket.receive_json()
                        event_type = event.get("event") if isinstance(event, dict) else None
                        if event_type == "start":
                            start = event.get("start", {})
                            if start.get("callSid") != expected_call_sid:
                                client_active = False
                                logger.warning("assemblyai_twilio_call_sid_mismatch")
                                await websocket.close(code=1008, reason="Twilio call identity did not match")
                                return
                            candidate = start.get("streamSid") or event.get("streamSid")
                            if isinstance(candidate, str) and candidate:
                                stream_sid = candidate
                                twilio_ready.set()
                        elif event_type == "media" and event.get("media", {}).get("track", "inbound") == "inbound":
                            payload = event.get("media", {}).get("payload")
                            if isinstance(payload, str):
                                await provider_ready.wait()
                                await _send_provider(provider, {"type": "input.audio", "audio": payload})
                        elif event_type == "stop":
                            client_active = False
                            await _send_provider(provider, {"type": "session.end"})
                            return
                except WebSocketDisconnect:
                    client_active = False
                    try:
                        await _send_provider(provider, {"type": "session.end"})
                    except _UPSTREAM_ERRORS:
                        pass

            async def from_provider() -> None:
                async for raw in provider:
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "session.ready":
                        logger.info(
                            "assemblyai_twilio_session_ready",
                            extra={"session_id": event.get("session_id"), "call_sid": expected_call_sid},
                        )
                        if resume_session_id:
                            provider_ready.set()
                        else:
                            await _send_provider(
                                provider,
                                {"type": "session.update", "session": {"tools": all_function_tool_configs()}},
                            )
                    elif event_type == "session.updated" and not resume_session_id:
                        provider_ready.set()
                    if event_type == "session.error":
                        if event.get("code") in {"session_not_found", "session_forbidden", "session_expired"}:
                            raise AssemblyAIResumeError("AssemblyAI refused to resume the session")
                        if not provider_ready.is_set():
                            raise AssemblyAIConfigurationError("AssemblyAI rejected the session tool configuration")
                    for result in await coordinator.handle(event):
                        await _send_provider(provider, result)
                    if event_type == "reply.audio" and isinstance(event.get("data"), str):
                        await twilio_ready.wait()
                        await websocket.send_json(
                            {"event": "media", "streamSid": stream_sid, "media": {"payload": event["data"]}}
                        )
                    elif event_type == "input.speech.started" and twilio_ready.is_set():
                        await websocket.send_json({"event": "clear", "streamSid": stream_sid})

            await _run_pair(from_twilio, from_provider)

    await _run_resumable(coordinator, connect_once)
    if not client_active:
        return


async def close_after_bridge(websocket: WebSocket, bridge: Awaitable[None]) -> None:
    """Map upstream failures to a generic WebSocket close without leaking credentials or response bodies."""
    try:
        await bridge
    except (*_UPSTREAM_ERRORS, json.JSONDecodeError, AssemblyAIResumeError, AssemblyAIConfigurationError):
        logger.exception("assemblyai_bridge_failed")
        try:
            await websocket.close(code=1011, reason="Voice service unavailable")
        except RuntimeError:
            pass
