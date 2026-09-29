import asyncio
import base64
import json

from preauth.api import live_captions


def test_caption_queue_is_bounded_and_stops_on_overload():
    caption = live_captions.LiveCaptions('secret', None)
    chunk = base64.b64encode(bytes(2400)).decode()
    for _ in range(150):
        caption.feed(chunk)
    assert caption.queue.qsize() == 100
    assert not caption.accepting
    assert caption.failed.is_set()


def test_caption_failure_is_isolated_from_main_call(monkeypatch):
    async def scenario():
        events = []
        async def send(event): events.append(event)
        def fail(*a, **kw): raise OSError('offline')
        monkeypatch.setattr(live_captions, 'connect', fail)
        task = asyncio.create_task(live_captions.LiveCaptions('secret', send).run())
        await asyncio.sleep(1.6)
        assert events[-1] == {'type': 'caption.unavailable'}
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_caption_stream_forwards_words_and_terminates_on_cancellation(monkeypatch):
    async def scenario():
        events, sent = [], []
        class Provider:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def recv(self): return json.dumps({'type': 'Begin'})
            async def send(self, value): sent.append(value)
            async def __aiter__(self):
                yield json.dumps({'type': 'Turn', 'transcript': 'my name', 'turn_order': 4, 'end_of_turn': False})
                await asyncio.Future()
        async def send(event): events.append(event)
        monkeypatch.setattr(live_captions, 'connect', lambda *a, **kw: Provider())
        caption = live_captions.LiveCaptions('secret', send)
        task = asyncio.create_task(caption.run())
        # A short WAV tail and the next packet must be combined, never sent as
        # a sub-50ms frame (provider error 3007 observed in the live soak).
        caption.feed(base64.b64encode(bytes(696)).decode())
        caption.feed(base64.b64encode(bytes(1704)).decode())
        await asyncio.sleep(.01)
        assert events[-1] == {'type': 'caption.preview', 'text': 'my name', 'turn': 4, 'final': False}
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert bytes(2400) in sent
        assert [len(value) for value in sent if isinstance(value, bytes)] == [2400]
        assert json.loads(sent[-1]) == {'type': 'Terminate'}
        assert not caption.accepting
    asyncio.run(scenario())
