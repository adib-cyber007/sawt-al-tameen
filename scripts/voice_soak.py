"""Live synthetic-audio soak: no microphone, transcript text, or credentials logged.

Usage: python scripts/voice_soak.py wss://host/api/v1/voice/assemblyai/browser sample.wav --seconds 480
The fixture must contain only non-sensitive test speech (PCM16, mono, 24 kHz).
This exercises Voice Agent audio, conversation captions, and corrections.
It does not reproduce physical speaker echo or browser device behavior.
"""

import argparse
import asyncio
import base64
import json
import statistics
import time
import wave
from collections import Counter

from websockets.asyncio.client import connect


async def run(url: str, fixture: str, seconds: int) -> None:
    with wave.open(fixture) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (24000, 1, 2):
            raise ValueError("Expected mono PCM16 24 kHz WAV")
        pcm = audio.readframes(audio.getnframes())
    start = time.monotonic()
    counts: Counter = Counter()
    gaps: list[float] = []
    last_partial = None
    done = asyncio.Event()
    ready = asyncio.Event()
    speech_queue: asyncio.Queue = asyncio.Queue()
    turns = 0
    correction_sent = False
    async with connect(url, open_timeout=15) as socket:
        async def receive():
            nonlocal last_partial
            async for raw in socket:
                event = json.loads(raw)
                kind = event.get("type")
                counts[kind] += 1
                if kind == "session.ready":
                    ready.set()
                    print(json.dumps({"session": event["session_id"]}), flush=True)
                if kind == "session.error":
                    raise RuntimeError(f"Live test failed: {kind} {event.get('code', '')}")
                if kind == "reply.done":
                    done.set()
                if kind == "transcript.user.delta":
                    now = time.monotonic()
                    if last_partial:
                        gaps.append(now - last_partial)
                    last_partial = now
                if kind == "transcript.user":
                    last_partial = None
            raise RuntimeError("Connection closed before the soak completed")

        async def feed():
            await ready.wait()
            current = b""
            offset = 0
            next_packet = time.monotonic()
            while True:
                if offset >= len(current) and not speech_queue.empty():
                    current, offset = speech_queue.get_nowait(), 0
                chunk = current[offset:offset + 2400].ljust(2400, b"\0") if offset < len(current) else bytes(2400)
                offset += len(chunk)
                await socket.send(json.dumps({"type": "input.audio", "audio": base64.b64encode(chunk).decode()}))
                next_packet += .05
                await asyncio.sleep(max(0, next_packet - time.monotonic()))

        async def converse():
            nonlocal turns, correction_sent
            await asyncio.wait_for(done.wait(), 30)
            while time.monotonic() - start < seconds:
                done.clear()
                before = counts["reply.audio"]
                if turns == 2:
                    correction_sent = True
                    await socket.send(json.dumps({"type": "conversation.correction",
                        "text": "My name is Alex. I only need general information, not a member-specific decision. Please answer briefly."}))
                else:
                    speech_queue.put_nowait(pcm)
                    await asyncio.sleep(len(pcm) / 48000 + 1)
                await asyncio.wait_for(done.wait(), 60)
                if counts["reply.audio"] <= before:
                    raise RuntimeError("Turn completed without spoken audio")
                turns += 1
                print(json.dumps({"elapsed_seconds": round(time.monotonic() - start), "turns": turns,
                                  "caller_partial_updates": counts["transcript.user.delta"]}), flush=True)
                await asyncio.sleep(3)

        reader = asyncio.create_task(receive())
        sender = asyncio.create_task(feed())
        conversation = asyncio.create_task(converse())
        try:
            completed, _ = await asyncio.wait([reader, sender, conversation], return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                task.result()
            if conversation not in completed:
                raise RuntimeError("Audio pump stopped early")
            if correction_sent:
                assert counts["correction.accepted"] == 1
            assert counts["transcript.user.delta"] > 0
            print(json.dumps({"result": "passed", "duration_seconds": round(time.monotonic() - start),
                "spoken_turns": turns, "events": dict(counts),
                "median_caller_partial_gap_ms": round(statistics.median(gaps) * 1000) if gaps else None}), flush=True)
        finally:
            sender.cancel()
            conversation.cancel()
            try:
                await socket.send(json.dumps({"type": "session.end"}))
            finally:
                reader.cancel()
                await asyncio.gather(reader, sender, conversation, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("wav")
    parser.add_argument("--seconds", type=int, default=480)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.wav, args.seconds))
