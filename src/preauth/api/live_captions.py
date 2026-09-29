"""Best-effort word-level preview, independent of the voice agent's transcript.

Never feed this second recognizer into business decisions or the authoritative
call record. A slow/failing caption connection must not stall caller audio.
"""

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect

logger = logging.getLogger("preauth.voice.captions")
CAPTION_URL = (
    "wss://streaming.assemblyai.com/v3/ws?sample_rate=24000"
    "&speech_model=universal-streaming-english&format_turns=false"
)


class LiveCaptions:
    def __init__(self, api_key: str, send: Callable[[dict], Awaitable[None]]):
        self.api_key = api_key
        self.send = send
        # The tunnel can deliver >1 second of healthy audio in one event-loop
        # batch. Allow five seconds, still bounded independently of call length.
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self.failed = asyncio.Event()
        self.accepting = True

    def feed(self, encoded: str) -> None:
        if not self.accepting:
            return
        try:
            self.queue.put_nowait(base64.b64decode(encoded, validate=True))
        except (asyncio.QueueFull, ValueError):
            # Dropping random audio would produce misleading words. Stop this
            # preview instead; the authoritative agent transcript continues.
            self.accepting = False
            self.failed.set()

    async def run(self) -> None:
        for attempt in range(3):
            if attempt:
                await self.send({"type": "caption.reconnecting"})
                await asyncio.sleep(0.5 * attempt)
                self.failed.clear()
                self.accepting = True
            await self._stream()
        await self.send({"type": "caption.unavailable"})
        # A preview failure must not complete the main voice call.
        await asyncio.Future()

    async def _stream(self) -> None:
        tasks: list[asyncio.Task] = []
        try:
            async with connect(CAPTION_URL, additional_headers={"Authorization": self.api_key},
                               open_timeout=8, close_timeout=2, ping_interval=20) as provider:
                first = json.loads(await asyncio.wait_for(provider.recv(), 8))
                if first.get("type") != "Begin":
                    raise RuntimeError("Caption service did not start")
                await self.send({"type": "caption.ready"})

                async def transmit():
                    pending = bytearray()
                    while True:
                        pending.extend(await self.queue.get())
                        # Streaming STT rejects packets shorter than 50 ms,
                        # including the final remainder of a WAV/test utterance.
                        while len(pending) >= 2400:
                            await provider.send(bytes(pending[:2400]))
                            del pending[:2400]

                async def receive():
                    async for raw in provider:
                        event = json.loads(raw)
                        if event.get("type") == "Turn" and event.get("transcript"):
                            await self.send({"type": "caption.preview", "text": event["transcript"],
                                "turn": event.get("turn_order"), "final": bool(event.get("end_of_turn"))})
                        elif event.get("type") in {"Error", "Termination"}:
                            logger.warning("live_caption_provider_ended", extra={"provider_event": event.get("type"),
                                "provider_code": event.get("error_code"),
                                "audio_seconds": event.get("audio_duration_seconds")})
                            return

                tasks = [asyncio.create_task(transmit()), asyncio.create_task(receive()),
                         asyncio.create_task(self.failed.wait())]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    logger.warning("live_caption_stream_reset", extra={"backlog_full": self.failed.is_set()})
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    # Stop metered STT even when the main voice call is cancelled.
                    try:
                        await asyncio.wait_for(provider.send(json.dumps({"type": "Terminate"})), 1)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("live_caption_unavailable", extra={"error_type": type(exc).__name__})
        finally:
            self.accepting = False
            while not self.queue.empty():
                self.queue.get_nowait()
