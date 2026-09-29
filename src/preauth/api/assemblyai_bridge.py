"""Server-owned WebSocket bridge for AssemblyAI browser and Twilio voice sessions.

Keeping this bridge server-side prevents the AssemblyAI API key and business-tool credentials from reaching a
browser. It also binds every function call to AssemblyAI's authoritative session id, which lets the application
enforce its transcript-before-signoff invariant after the call.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from preauth.agent_tools.assemblyai import all_function_tool_configs
from preauth.agent_tools.voice_gateway import VoiceToolGateway
from preauth.infrastructure.settings import Settings
from preauth.api.live_captions import LiveCaptions

logger = logging.getLogger("preauth.voice.assemblyai.bridge")
RESUME_WINDOW_SECONDS = 30
MAX_RESUME_ATTEMPTS = 3
TOOL_TIMEOUT_SECONDS = 15
MAX_PENDING_TOOLS = 32
_BACKGROUND_TOOLS: set[asyncio.Task] = set()


class AssemblyAIResumeError(RuntimeError):
    """The provider session could not be resumed without losing correlation or conversation context."""


class AssemblyAIConfigurationError(RuntimeError):
    """The provider rejected the client-side tool configuration for a new session."""


class AssemblyAITransientError(RuntimeError):
    """The provider reported a retryable session failure."""


class AssemblyAISessionError(RuntimeError):
    """The provider reported a terminal session failure."""


_UPSTREAM_ERRORS = (
    ConnectionClosed,
    WebSocketException,
    TimeoutError,
    OSError,
    AssemblyAITransientError,
)

_RESUME_REFUSALS = {"session_not_found", "session_forbidden", "session_expired"}
_TRANSIENT_SESSION_ERRORS = {"server_error", "agent_init_failed", "agent_timeout", "internal_error", "at_capacity", "concurrency_exceeded"}
_CONFIGURATION_SESSION_ERRORS = {"invalid_format", "invalid_value", "immutable_field", "invalid_configuration"}


@dataclass
class PendingTool:
    call_id: str
    generation: int
    result: dict[str, Any] | None = None
    started: bool = False
    expired: bool = False


class AssemblyAIToolCoordinator:
    """Serial business execution, independent reception, generation-bound result dispatch."""

    def __init__(self, gateway: VoiceToolGateway):
        self._gateway = gateway
        self.session_id: str | None = None
        self.ready_count = 0
        self._pending: list[PendingTool] = []
        self._last_tool_task: asyncio.Task[dict[str, Any]] | None = None
        self._generation = 0
        self._safe = False
        self._turn_finished = False
        self._changed = asyncio.Event()
        self._monitors: set[asyncio.Task] = set()
        self._executions: set[asyncio.Task] = set()
        self._closed = False

    async def _execute_tool(
        self, call_id: str, name: str, arguments: dict[str, Any],
        previous: asyncio.Task[dict[str, Any]] | None, session_id: str, pending: PendingTool,
    ) -> dict[str, Any]:
        # Business tools retain their original call order, but a slow tool must not stall
        # the provider receive pump and interrupt an in-progress spoken reply.
        if previous is not None:
            await asyncio.shield(previous)
        if self._closed or pending.expired or pending.generation != self._generation:
            return {}  # Queued work that never started has no side effects to preserve.
        try:
            pending.started = True
            response = await asyncio.to_thread(self._gateway.call, name, arguments, session_id, provider_call_id=call_id)
            value = response.model_dump(mode="json")
            is_error = not response.ok
        except Exception:
            logger.exception("assemblyai_tool_call_unexpected_failure", extra={"tool": name})
            value = {
                "ok": False,
                "error": {"code": "TOOL_EXECUTION_FAILED", "message": "The operation's outcome could not be confirmed."},
                "guidance": "Retry the same arguments and request identity. Do not assume cancellation or open a separate request.",
            }
            is_error = True
        return {
            "type": "tool.result",
            "call_id": call_id,
            "result": json.dumps(value, separators=(",", ":")),
            "is_error": is_error,
        }

    async def _collect(self, pending: PendingTool, task: asyncio.Task) -> None:
        try:
            pending.result = await asyncio.wait_for(asyncio.shield(task), TOOL_TIMEOUT_SECONDS)
        except TimeoutError:
            # A timeout is not transaction cancellation. Keep the execution task alive and
            # chained ahead of later operations; its eventual response is durable.
            pending.expired = True
            pending.result = {
                "type": "tool.result", "call_id": pending.call_id, "is_error": True,
                "result": json.dumps({"ok": False, "error": {"code": "TOOL_TIMEOUT",
                    "message": ("The operation is still pending; its outcome is not yet known." if pending.started
                                else "The queued operation exceeded its deadline and did not start.")},
                    "guidance": "Do not claim failure or start a new request. Retry the same arguments and request key."}),
            }
        self._changed.set()

    def disconnected(self) -> None:
        self._safe = False
        self._changed.set()

    async def close(self) -> None:
        self._closed = True
        self._safe = False
        self._generation += 1
        self._pending.clear()
        for task in self._monitors:
            task.cancel()
        await asyncio.gather(*self._monitors, return_exceptions=True)

    async def dispatch(self, provider: Any) -> None:
        while True:
            await self._changed.wait()
            self._changed.clear()
            for pending in list(self._pending):
                if not self._safe:
                    break
                if pending.generation != self._generation or pending.result is None:
                    continue
                # Recheck after every send; the receiver can invalidate the generation
                # while a prior send is suspended on network backpressure.
                await _send_provider(provider, pending.result)
                logger.info("assemblyai_tool_result_sent", extra={"session_id": self.session_id, "call_id": pending.call_id})
                if pending in self._pending:
                    self._pending.remove(pending)

    async def handle(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type in {"input.speech.started", "input.speech.stopped", "transcript.user", "reply.started", "reply.done", "tool.call"}:
            logger.info("assemblyai_voice_event", extra={"session_id": self.session_id,
                "voice_event": event_type, "reply_id": event.get("reply_id"),
                "call_id": event.get("call_id"), "reply_status": event.get("status"),
                "text_length": len(event.get("text", "")), "pending_tools": len(self._pending)})
        if event_type == "session.ready":
            self._safe = False
            session_id = event.get("session_id")
            if isinstance(session_id, str) and session_id:
                if self.session_id and self.session_id != session_id:
                    raise AssemblyAIResumeError("Provider changed session identity during resume")
                self.session_id = session_id
                self.ready_count += 1
            return

        if event_type == "reply.done" and event.get("status") == "interrupted":
            self._generation += 1
            self._pending.clear()
            self._safe = False
            self._turn_finished = False
            return

        if event_type in {"reply.started", "input.speech.started"}:
            # Backchannels and transition phrases pause dispatch, not execution.
            # Only an explicit interrupted reply invalidates pending work.
            self._safe = False

        if event_type == "tool.call":
            call_id, name, arguments = event.get("call_id"), event.get("name"), event.get("arguments")
            if not self.session_id or not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not isinstance(arguments, dict):
                logger.warning("assemblyai_tool_call_malformed")
                return
            if any(p.call_id == call_id for p in self._pending):
                return
            if len(self._pending) >= MAX_PENDING_TOOLS or len(self._executions) >= MAX_PENDING_TOOLS:
                raise AssemblyAISessionError("Too many pending tool calls")
            pending = PendingTool(call_id, self._generation)
            task = asyncio.create_task(self._execute_tool(call_id, name, arguments, self._last_tool_task, self.session_id, pending))
            self._executions.add(task)
            task.add_done_callback(self._executions.discard)
            _BACKGROUND_TOOLS.add(task)
            task.add_done_callback(_BACKGROUND_TOOLS.discard)
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            self._last_tool_task = task
            self._pending.append(pending)
            if self._safe:
                # AssemblyAI may emit tool.call just after reply.done. The
                # completed reply is already a valid result boundary.
                self._changed.set()
            monitor = asyncio.create_task(self._collect(pending, task))
            self._monitors.add(monitor)
            monitor.add_done_callback(self._monitors.discard)
            return

        if event_type == "reply.done":
            # A completed reply is a safe boundary, including when tool.call
            # arrives after it. Do not require a particular reply-id shape.
            self._safe = event.get("status") in (None, "completed")
            self._turn_finished = self._safe
            self._changed.set()


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


async def _run_pair(*pumps: Callable[[], Awaitable[None]]) -> None:
    tasks = {asyncio.create_task(pump()) for pump in pumps}
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        # Also drain both pumps when the enclosing request is cancelled (for example on shutdown).
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
            if deadline is None:
                deadline = now + RESUME_WINDOW_SECONDS
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


async def bridge_browser(websocket: WebSocket, settings: Settings, gateway: VoiceToolGateway,
                         record_correction: Callable[[str, str], str] | None = None) -> None:
    coordinator = AssemblyAIToolCoordinator(gateway)
    client_active = True
    captions = LiveCaptions(settings.assemblyai_api_key, websocket.send_json) if settings.assemblyai_live_captions else None

    async def connect_once(resume_session_id: str | None) -> None:
        nonlocal client_active
        ready = asyncio.Event()
        tools_requested = False
        if resume_session_id:
            await websocket.send_json({"type": "connection.reconnecting"})
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
                last_text_at = 0.0
                last_retry_at = 0.0
                try:
                    while True:
                        event = await websocket.receive_json()
                        event_type = event.get("type") if isinstance(event, dict) else None
                        if event_type == "input.audio" and isinstance(event.get("audio"), str):
                            await ready.wait()
                            if captions:
                                captions.feed(event["audio"])
                            await _send_provider(provider, {"type": "input.audio", "audio": event["audio"]})
                        elif event_type == "connection.ping":
                            await websocket.send_json({"type": "connection.pong"})
                        elif event_type == "conversation.correction":
                            content = event.get("text")
                            now = time.monotonic()
                            if not isinstance(content, str) or not content.strip() or len(content) > 1000:
                                await websocket.send_json({"type": "correction.error", "message": "Enter a correction of 1–1000 characters."})
                                continue
                            if not ready.is_set() or now - last_text_at < 2:
                                await websocket.send_json({"type": "correction.error", "message": "Please wait a moment and send again."})
                                continue
                            last_text_at = now
                            try:
                                if record_correction is None:
                                    raise RuntimeError("Correction storage unavailable")
                                await asyncio.to_thread(record_correction, coordinator.session_id, content.strip())
                            except Exception as exc:
                                logger.warning("voice_correction_storage_failed", extra={"error_type": type(exc).__name__})
                                await websocket.send_json({"type": "correction.error", "message": "The correction could not be saved. Please try again."})
                                continue
                            # Never accept a client-supplied role, prompt, or tool result.
                            await _send_provider(provider, {"type": "conversation.message", "role": "user",
                                "content": "Correction to what I said: " + content.strip()})
                            await _send_provider(provider, {"type": "reply.create",
                                "instructions": "Acknowledge the caller's latest correction briefly. Ask only the next needed question. Do not repeat completed operations."})
                            await websocket.send_json({"type": "correction.accepted", "text": content.strip()})
                        elif event_type == "reply.retry" and ready.is_set():
                            now = time.monotonic()
                            if now - last_retry_at < 20:
                                continue
                            last_retry_at = now
                            await _send_provider(provider, {"type": "reply.create",
                                "instructions": "The caller is waiting because the reply stalled. Briefly answer their latest request using the current conversation. If unclear, ask them to repeat only the missing detail. Do not repeat completed operations or claim pending operations succeeded."})
                            logger.info("assemblyai_reply_recovery_requested", extra={"session_id": coordinator.session_id})
                            await websocket.send_json({"type": "reply.retrying"})
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
                nonlocal tools_requested
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
                            tools_requested = True
                            await _send_provider(
                                provider,
                                {
                                    "type": "session.update",
                                    "session": {
                                        "tools": all_function_tool_configs(),
                                        "input": {"turn_detection": {"interrupt_response": True}},
                                    },
                                },
                            )
                    elif event_type == "session.updated" and tools_requested and not ready.is_set():
                        ready.set()
                        await websocket.send_json({"type": "session.ready", "session_id": coordinator.session_id})
                    if event_type == "session.error":
                        code = event.get("code")
                        logger.warning(
                            "assemblyai_browser_session_error",
                            extra={"session_id": coordinator.session_id, "provider_code": code},
                        )
                        if code in _RESUME_REFUSALS:
                            raise AssemblyAIResumeError("AssemblyAI refused to resume the session")
                        if code in _TRANSIENT_SESSION_ERRORS:
                            raise AssemblyAITransientError("AssemblyAI reported a transient session failure")
                        if not ready.is_set() or code in _CONFIGURATION_SESSION_ERRORS:
                            raise AssemblyAIConfigurationError("AssemblyAI rejected the session tool configuration")
                        raise AssemblyAISessionError("AssemblyAI reported a terminal session failure")
                    await coordinator.handle(event)
                    safe = _safe_browser_event(event)
                    # Let microphone capture begin only once tools are attached.
                    if safe is not None and not (event_type == "session.ready" and not resume_session_id):
                        await websocket.send_json(safe)

            try:
                await _run_pair(from_browser, from_provider, lambda: coordinator.dispatch(provider))
            finally:
                coordinator.disconnected()

    caption_task = asyncio.create_task(captions.run()) if captions else None
    try:
        await _run_resumable(coordinator, connect_once)
    finally:
        if caption_task:
            caption_task.cancel()
            await asyncio.gather(caption_task, return_exceptions=True)
        await coordinator.close()
    if not client_active:
        return


async def bridge_twilio(
    websocket: WebSocket, settings: Settings, gateway: VoiceToolGateway, expected_call_sid: str, stream_sid: str
) -> None:
    coordinator = AssemblyAIToolCoordinator(gateway)
    client_active = True

    async def connect_once(resume_session_id: str | None) -> None:
        nonlocal client_active
        provider_ready = asyncio.Event()
        tools_requested = False
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
                nonlocal client_active
                try:
                    while True:
                        event = await websocket.receive_json()
                        event_type = event.get("event") if isinstance(event, dict) else None
                        if event_type == "start" or event.get("streamSid", stream_sid) != stream_sid:
                            client_active = False
                            await websocket.close(code=1008, reason="Twilio stream identity did not match")
                            return
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
                nonlocal tools_requested
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
                            tools_requested = True
                            await _send_provider(
                                provider,
                                {"type": "session.update", "session": {"tools": all_function_tool_configs()}},
                            )
                    elif event_type == "session.updated" and tools_requested and not provider_ready.is_set():
                        provider_ready.set()
                    if event_type == "session.error":
                        code = event.get("code")
                        logger.warning(
                            "assemblyai_twilio_session_error",
                            extra={
                                "session_id": coordinator.session_id,
                                "call_sid": expected_call_sid,
                                "provider_code": code,
                            },
                        )
                        if code in _RESUME_REFUSALS:
                            raise AssemblyAIResumeError("AssemblyAI refused to resume the session")
                        if code in _TRANSIENT_SESSION_ERRORS:
                            raise AssemblyAITransientError("AssemblyAI reported a transient session failure")
                        if not provider_ready.is_set() or code in _CONFIGURATION_SESSION_ERRORS:
                            raise AssemblyAIConfigurationError("AssemblyAI rejected the session tool configuration")
                        raise AssemblyAISessionError("AssemblyAI reported a terminal session failure")
                    await coordinator.handle(event)
                    if event_type == "reply.audio" and isinstance(event.get("data"), str):
                        await websocket.send_json(
                            {"event": "media", "streamSid": stream_sid, "media": {"payload": event["data"]}}
                        )
                    elif event_type == "input.speech.started" or (
                        event_type == "reply.done" and event.get("status") == "interrupted"
                    ):
                        await websocket.send_json({"event": "clear", "streamSid": stream_sid})

            try:
                await _run_pair(from_twilio, from_provider, lambda: coordinator.dispatch(provider))
            finally:
                coordinator.disconnected()

    try:
        await _run_resumable(coordinator, connect_once)
    finally:
        await coordinator.close()
    if not client_active:
        return


async def close_after_bridge(websocket: WebSocket, bridge: Awaitable[None]) -> None:
    """Map upstream failures to a generic WebSocket close without leaking credentials or response bodies."""
    try:
        await bridge
    except (
        *_UPSTREAM_ERRORS,
        json.JSONDecodeError,
        AssemblyAIResumeError,
        AssemblyAIConfigurationError,
        AssemblyAISessionError,
    ):
        logger.exception("assemblyai_bridge_failed")
        try:
            await websocket.close(code=1011, reason="Voice service unavailable")
        except RuntimeError:
            pass
