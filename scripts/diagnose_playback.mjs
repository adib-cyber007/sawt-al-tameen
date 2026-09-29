// Measure a synthetic greeting without sending or saving microphone audio.
// Usage: node scripts/diagnose_playback.mjs ws://localhost:8000/api/v1/voice/assemblyai/browser
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const url = process.argv[2];
if (!url) throw Error('Supply the browser voice WebSocket URL');
const events = [];
const started = performance.now();
const socket = new WebSocket(url);
const timeout = setTimeout(() => { console.error('Greeting timed out'); socket.close(); process.exitCode = 1; }, 30000);
socket.addEventListener('error', () => { clearTimeout(timeout); console.error('Voice connection failed'); process.exitCode = 1; });
socket.addEventListener('message', ({ data }) => {
  const event = JSON.parse(String(data));
  if (event.type === 'session.error') {
    console.error('Provider error:', event.code);
    process.exitCode = 1;
    clearTimeout(timeout);
    socket.close();
  }
  if (event.type === 'reply.audio') events.push({ at: performance.now() - started, pcm: Buffer.from(event.data, 'base64') });
  if (event.type !== 'reply.done') return;
  clearTimeout(timeout);
  socket.send(JSON.stringify({ type: 'session.end' }));
  socket.close();
  if (!events.length) throw Error('No greeting audio received');
  const doneAt = performance.now() - started;
  const gaps = events.slice(1).map((event, index) => event.at - events[index].at);
  const pcm = Buffer.concat(events.map(event => event.pcm));
  const silenceRuns = [];
  let silenceStart;
  for (let offset = 0; offset + 480 <= pcm.length; offset += 480) {
    let sum = 0;
    for (let i = offset; i < offset + 480; i += 2) sum += (pcm.readInt16LE(i) / 32768) ** 2;
    const quiet = Math.sqrt(sum / 240) < 0.003;
    const ms = offset / 48;
    if (quiet && silenceStart === undefined) silenceStart = ms;
    if (!quiet && silenceStart !== undefined) {
      if (ms - silenceStart >= 200) silenceRuns.push({ startMs: silenceStart, durationMs: ms - silenceStart });
      silenceStart = undefined;
    }
  }
  let Processor;
  let firstRenderedFrame;
  const bufferingAfterStart = [];
  const context = vm.createContext({ sampleRate: 48000, currentFrame: 0, Int16Array,
    AudioWorkletProcessor: class { port = { postMessage(event) {
      if (event.state === 'speaking' && firstRenderedFrame === undefined) firstRenderedFrame = context.currentFrame;
      if (event.state === 'buffering' && firstRenderedFrame !== undefined) bufferingAfterStart.push(context.currentFrame / 48);
    } }; },
    registerProcessor: (_, implementation) => { Processor = implementation; },
  });
  const source = readFileSync(new URL('../src/preauth/hosted_web/pcm-playback.js', import.meta.url), 'utf8');
  vm.runInContext(source, context);
  const player = new Processor({ processorOptions: { inputSampleRate: 24000 } });
  const first = events[0].at;
  let next = 0;
  let flushed = false;
  const output = new Float32Array(128);
  const rendered = [];
  for (let frame = 0; frame < (doneAt - first + pcm.length / 48 + 3000) * 48; frame += 128) {
    context.currentFrame = frame;
    const now = first + frame / 48;
    while (next < events.length && events[next].at <= now) {
      const bytes = Uint8Array.from(events[next++].pcm);
      player.port.onmessage({ data: { type: 'audio', samples: bytes.buffer } });
    }
    if (!flushed && now >= doneAt) { player.port.onmessage({ data: { type: 'flush' } }); flushed = true; }
    player.process([], [[output]]);
    rendered.push(...output);
  }
  let mismatches = 0;
  const samples = new Int16Array(Uint8Array.from(pcm).buffer);
  for (let i = 0; i < samples.length * 2; i++) {
    const left = Math.floor(i / 2);
    const expected = (samples[left] + ((samples[left + 1] ?? samples[left]) - samples[left]) * (i % 2) / 2) / 32768;
    const actual = rendered[firstRenderedFrame + i];
    if (!Number.isFinite(actual) || Math.abs(actual - expected) > 0.000001) mismatches++;
  }
  console.log(JSON.stringify({ status: event.status, packets: events.length, audioMs: pcm.length / 48,
    deliveryMs: Math.round(doneAt - first), maxPacketGapMs: Math.round(Math.max(...gaps)),
    packetGapsOver100ms: gaps.filter(gap => gap > 100).map(Math.round),
    playbackStartMs: firstRenderedFrame / 48, playbackMatchesSource: firstRenderedFrame !== undefined && mismatches === 0,
    totalStartMs: Math.round(first + firstRenderedFrame / 48), bufferingAfterStart,
    mismatchedOutputSamples: mismatches, silenceInsideGeneratedAudio: silenceRuns }, null, 2));
});
