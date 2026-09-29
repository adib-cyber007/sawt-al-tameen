import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const root = new URL('../', import.meta.url);
const worklet = readFileSync(new URL('src/preauth/hosted_web/pcm-capture.js', root), 'utf8');
const playbackWorklet = readFileSync(new URL('src/preauth/hosted_web/pcm-playback.js', root), 'utf8');
const app = readFileSync(new URL('src/preauth/hosted_web/app.js', root), 'utf8');

for (const rate of [16000, 24000, 44100, 48000, 96000]) {
  test(`PCM conversion preserves timing and samples at ${rate} Hz`, () => {
    let Processor;
    const chunks = [];
    vm.runInNewContext(worklet, {
      sampleRate: rate,
      AudioWorkletProcessor: class {
        port = { postMessage: buffer => chunks.push(new Int16Array(buffer)) };
      },
      registerProcessor: (_, implementation) => { Processor = implementation; },
    });
    const processor = new Processor({ processorOptions: { inputSampleRate: rate } });
    // Deliberately irregular block boundaries expose phase discontinuities.
    for (let offset = 0; offset < rate + 16;) {
      const length = Math.min(127, rate + 16 - offset);
      const input = Float32Array.from({ length }, (_, i) => ((offset + i) % 1000) / 1000);
      processor.process([[input]]);
      offset += length;
    }
    assert.equal(chunks.length, 20);
    const output = chunks.flatMap(chunk => Array.from(chunk));
    assert.equal(output.length, 24000);
    for (let i = 0; i < output.length; i++) {
      const position = i * rate / 24000;
      const left = Math.floor(position);
      const fraction = position - left;
      const expected = Math.round(((left % 1000) + (((left + 1) % 1000) - (left % 1000)) * fraction) / 1000 * 32767);
      assert.ok(Math.abs(output[i] - expected) <= 1, `sample ${i}: ${output[i]} vs ${expected}`);
    }
  });
}

function playback(rate) {
  let Processor;
  vm.runInNewContext(playbackWorklet, {
    sampleRate: rate,
    Int16Array,
    AudioWorkletProcessor: class { port = { onmessage: null, postMessage() {} }; },
    registerProcessor: (_, implementation) => { Processor = implementation; },
  });
  const processor = new Processor({ processorOptions: { inputSampleRate: 24000 } });
  const send = (type, samples) => processor.port.onmessage({ data: {
    type, samples: samples?.buffer,
  } });
  const render = (length) => {
    const output = new Float32Array(length);
    processor.process([], [[output]]);
    return output;
  };
  return { processor, send, render };
}

test('playback buffers short packets and produces continuous output at device rate', () => {
  const { send, render } = playback(48000);
  for (let i = 0; i < 74; i++) send('audio', new Int16Array(240).fill(16384));
  assert.ok(render(128).every(value => value === 0));
  send('audio', new Int16Array(240).fill(16384));
  assert.ok(render(48000).every(value => value === 0), 'do not start an incomplete reply');
  send('flush');
  const output = render(128);
  assert.ok(output.every(value => value === 0.5));
  send('audio', new Int16Array(240).fill(16384));
  assert.ok(render(128).every(value => value === 0.5));
});

test('playback resamples across packet boundaries without a discontinuity', () => {
  const { send, render } = playback(48000);
  send('audio', new Int16Array(240).fill(0));
  send('audio', new Int16Array(240).fill(16384));
  send('flush');
  const output = render(481);
  assert.equal(output[478], 0);
  assert.equal(output[479], 0.25);
  assert.equal(output[480], 0.5);
});

test('clearing playback discards completed and incomplete replies', () => {
  const { processor, send, render } = playback(24000);
  send('audio', new Int16Array(6000).fill(16384));
  send('flush');
  render(128);
  send('audio', new Int16Array(240).fill(16384));
  send('clear');
  assert.equal(processor.buffered, 0);
  assert.ok(render(128).every(value => value === 0));
  send('audio', new Int16Array(240).fill(-16384));
  send('flush');
  assert.ok(render(128).every(value => value === -0.5));
});

test('a second incomplete reply cannot undo the first reply completion or start early', () => {
  const { processor, send, render } = playback(24000);
  send('audio', new Int16Array(2400).fill(16384));
  send('flush');
  send('audio', new Int16Array(2400).fill(-16384));
  assert.ok(render(2400).every(value => value === 0.5));
  assert.ok(render(48000).every(value => value === 0));
  assert.equal(processor.state, 'buffering');
  send('audio', new Int16Array(2400).fill(-16384));
  send('flush');
  assert.ok(render(4800).every(value => value === -0.5));
  assert.equal(processor.buffered, 0);
  assert.equal(processor.state, 'idle');
});

function browser() {
  const elements = new Map();
  const context = vm.createContext({
    document: { querySelector: selector => {
      if (!elements.has(selector)) elements.set(selector, {
        classList: { toggle() {}, remove() {} }, addEventListener() {},
      });
      return elements.get(selector);
    } },
    WebSocket: { OPEN: 1, CLOSING: 2 },
  });
  vm.runInContext(app, context);
  return { context, elements };
}

for (const rate of [24000, 44100, 48000, 96000]) {
  test(`replies have no inserted pauses despite a 2.1 second delivery stall at ${rate} Hz`, () => {
    const { processor, send, render } = playback(rate);
    // A completed short greeting must not leave the next reply in the old
    // 20 ms resume mode, which made it start and stop on individual bursts.
    send('audio', new Int16Array(240).fill(16384));
    send('flush');
    render(Math.ceil(rate * 0.02));
    assert.equal(processor.replyActive, false);
    const received = [];
    let packet = 0;
    const arrival = Array.from({ length: 20 }, (_, index) =>
      index < 8 ? index * 0.1 : index * 0.1 + 2.1);
    for (let frame = 0; frame < rate * 7; frame += 128) {
      const time = frame / rate;
      while (packet < arrival.length && time >= arrival[packet]) {
        send('audio', new Int16Array(2400).fill(16384));
        packet += 1;
        if (packet === 20) send('flush');
      }
      received.push(...render(128));
    }
    const first = received.findIndex(value => value !== 0);
    const last = received.findLastIndex(value => value !== 0);
    assert.ok(first >= Math.floor(rate * arrival.at(-1)), 'wait until the complete reply is available');
    assert.ok(received.slice(first, last + 1).every(value => value === 0.5), 'no gaps within the spoken reply');
    assert.ok(Math.abs(last - first + 1 - rate * 2) <= 1, 'preserve all two seconds of speech');
    assert.equal(processor.buffered, 0);
  });
}

test('long replies start with a three-second reserve and remain continuous across a 2.1 second stall', () => {
  const { send, render, processor } = playback(48000);
  const received = [];
  let packet = 0;
  for (let frame = 0; frame < 48000 * 12; frame += 128) {
    const now = frame / 48000;
    while (packet < 80 && now >= packet * 0.1 + (packet >= 40 ? 2.1 : 0)) {
      send('audio', new Int16Array(2400).fill(16384));
      if (++packet === 80) send('flush');
    }
    received.push(...render(128));
  }
  const first = received.findIndex(value => value !== 0);
  const last = received.findLastIndex(value => value !== 0);
  assert.ok(first / 48000 >= 2.9 && first / 48000 < 3.1, 'start before the whole reply arrives');
  assert.equal(last - first + 1, 48000 * 8);
  assert.ok(received.slice(first, last + 1).every(value => value === 0.5), 'no mid-sentence gaps');
  assert.equal(processor.replyActive, false);
});

test('slow deliveries start available speech after five seconds and retain the same reply after starvation', () => {
  const { send, render, processor } = playback(24000);
  send('audio', new Int16Array(2400).fill(16384));
  assert.ok(render(24000 * 5).every(value => value === 0));
  assert.ok(render(2400).every(value => value === 0.5));
  assert.equal(processor.replyActive, true, 'an unfinished streamed reply must retain its boundary');
  send('audio', new Int16Array(2400).fill(-16384));
  send('flush');
  assert.ok(render(2400).every(value => value === -0.5));
  assert.equal(processor.replyActive, false);
});

test('a completion event after streamed audio has drained leaves no stuck reply', () => {
  const { send, render, processor } = playback(24000);
  send('audio', new Int16Array(72000).fill(16384));
  assert.ok(render(72000).every(value => value === 0.5));
  send('flush');
  assert.ok(render(128).every(value => value === 0));
  assert.equal(processor.replyActive, false);
});

test('short replies play immediately once complete', () => {
  const { send, render } = playback(48000);
  send('audio', new Int16Array(240).fill(16384));
  assert.ok(render(128).every(value => value === 0));
  send('flush');
  assert.ok(render(480).every(value => value === 0.5));
  assert.ok(render(128).every(value => value === 0));
});

for (const rate of [16000, 24000, 44100, 48000, 96000]) {
  test(`speaker echo is suppressed through buffering, gaps, drain and tail at ${rate} Hz`, () => {
    const { processor: player, send } = playback(rate);
    let Capture;
    const chunks = [];
    const states = [];
    vm.runInNewContext(worklet, {
      sampleRate: rate,
      AudioWorkletProcessor: class {
        port = { postMessage: data => {
          if (data.type === 'capture.state') states.push(data.blocked);
          else chunks.push(new Int16Array(data));
        } };
      },
      registerProcessor: (_, implementation) => { Capture = implementation; },
    });
    const capture = new Capture({ processorOptions: { inputSampleRate: rate } });
    const render = () => {
      const speaker = new Float32Array(128);
      const gate = new Float32Array(128);
      player.process([], [[speaker], [gate]]);
      // A loud microphone signal models complete echo-cancellation failure.
      capture.process([[new Float32Array(128).fill(0.8)], [gate]]);
      return { speaker, gate };
    };
    send('audio', new Int16Array(240).fill(16000));
    assert.ok(render().gate.every(value => value === 1), 'gate before audible playback');
    for (let i = 0; i < 10; i++) send('audio', new Int16Array(240).fill(16000));
    for (let i = 0; i < Math.ceil(rate / 128); i++) {
      assert.ok(render().gate.every(value => value === 1), 'gate holds through a long network gap');
    }
    assert.ok(chunks.length > 0);
    assert.ok(chunks.every(chunk => chunk.every(value => value === 0)));
    send('audio', new Int16Array(12000).fill(16000));
    send('flush');
    while (player.replyActive) {
      assert.ok(render().gate.every(value => value === 1), 'reply.done does not unmute queued audio');
    }
    for (let i = 0; i < Math.floor(rate * 0.35 / 128); i++) render();
    assert.equal(capture.blocked, true, 'room echo tail is still protected');
    assert.ok(chunks.every(chunk => chunk.every(value => value === 0)));
    for (let i = 0; i < Math.ceil(rate * 0.1 / 128); i++) render();
    assert.equal(capture.blocked, false);
    assert.ok(chunks.at(-1).some(value => value > 20000), 'caller audio resumes');
    assert.deepEqual(states, [true, false]);
    send('audio', new Int16Array(240).fill(16000));
    render();
    assert.equal(capture.blocked, true, 'next reply reengages protection');
    send('clear');
    for (let i = 0; i < Math.ceil(rate * 0.4 / 128); i++) render();
    assert.equal(capture.blocked, false, 'clear releases protection after the echo tail');
  });
}

function liveBrowser() {
  const { context, elements } = browser();
  const nodes = [];
  let constraints;
  const track = { readyState: 'live', muted: false, stop() { this.readyState = 'ended'; } };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  const source = { connect: node => node };
  context.navigator = { mediaDevices: { getUserMedia: async value => {
    constraints = value;
    return stream;
  } } };
  context.location = { protocol: 'https:', host: 'example.test' };
  context.AudioContext = class {
    state = 'running';
    sampleRate = 48000;
    baseLatency = 0.01;
    outputLatency = 0.04;
    destination = {};
    audioWorklet = { addModule: async () => {} };
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return source; }
    createGain() { return { gain: {}, connect() {} }; }
  };
  context.AudioWorkletNode = class {
    port = { postMessage: message => this.messages.push(message) };
    messages = [];
    connections = [];
    constructor(_, name, options) { Object.assign(this, { name, options }); nodes.push(this); }
    connect(...args) { this.connections.push(args); return args[0]; }
    disconnect() {}
  };
  context.WebSocket = class {
    static OPEN = 1;
    static CLOSING = 2;
    readyState = 1;
    send() {}
    close() { this.readyState = 3; }
    constructor() { context.liveSocket = this; }
  };
  elements.get('#connection').classList.add = () => {};
  return { context, elements, nodes, track, stream, constraints: () => constraints };
}

test('browser wires the inaudible playback gate to capture and ignores false speech interruptions', async () => {
  const { context, elements, nodes, constraints } = liveBrowser();
  await vm.runInContext('startCall()', context);
  const [player, capture] = nodes;
  assert.equal(constraints().audio.echoCancellation, true);
  assert.equal(constraints().audio.noiseSuppression, true);
  assert.equal(player.options.numberOfOutputs, 2);
  assert.equal(capture.options.numberOfInputs, 2);
  assert.deepEqual(player.connections[1], [capture, 1, 1]);
  assert.ok(Math.abs(capture.options.processorOptions.echoTailSeconds - 0.4) < 0.001);
  context.liveSocket.onmessage({ data: JSON.stringify({ type: 'session.ready' }) });
  player.port.onmessage({ data: { type: 'playback.state', state: 'buffering' } });
  assert.match(elements.get('#status').textContent, /Preparing the reply/);
  capture.port.onmessage({ data: { type: 'capture.state', blocked: true } });
  assert.match(elements.get('#status').textContent, /Preparing the reply/);
  player.port.onmessage({ data: { type: 'playback.state', state: 'speaking' } });
  assert.match(elements.get('#status').textContent, /Assistant speaking/);
  context.liveSocket.onmessage({ data: JSON.stringify({ type: 'input.speech.started' }) });
  assert.equal(player.messages.length, 0, 'false VAD event must not cut off the assistant');
  context.liveSocket.onmessage({ data: JSON.stringify({ type: 'reply.done' }) });
  assert.equal(player.messages[0].type, 'flush');
  assert.match(elements.get('#status').textContent, /Assistant speaking/);
  capture.port.onmessage({ data: { type: 'capture.state', blocked: false } });
  player.port.onmessage({ data: { type: 'playback.state', state: 'idle' } });
  assert.match(elements.get('#status').textContent, /Listening/);
});

test('ending during permission request releases the late microphone without starting a connection', async () => {
  const { context, elements, nodes, track, stream } = liveBrowser();
  let grant;
  context.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const start = vm.runInContext('startCall()', context);
  assert.equal(elements.get('#end').disabled, false);
  await vm.runInContext('stopCall()', context);
  grant(stream);
  await start;
  assert.equal(track.readyState, 'ended');
  assert.equal(nodes.length, 0);
  assert.equal(context.liveSocket, undefined);
  assert.equal(elements.get('#start').disabled, false);
});

test('late permission rejection cannot tear down a subsequent call', async () => {
  const { context, elements, stream } = liveBrowser();
  let reject;
  context.navigator.mediaDevices.getUserMedia = () => new Promise((_, fail) => { reject = fail; });
  const oldStart = vm.runInContext('startCall()', context);
  await vm.runInContext('stopCall()', context);
  context.navigator.mediaDevices.getUserMedia = async () => stream;
  await vm.runInContext('startCall()', context);
  reject({ name: 'NotAllowedError' });
  await oldStart;
  assert.equal(context.liveSocket.readyState, 1);
  assert.equal(elements.get('#start').disabled, true);
});

test('phone interruption offers resume and muted microphones never display Listening', async () => {
  const { context, elements, track } = liveBrowser();
  await vm.runInContext('startCall()', context);
  context.liveSocket.onmessage({ data: '{"type":"session.ready"}' });
  vm.runInContext('audioContext.state = "interrupted"; audioContext.onstatechange()', context);
  assert.equal(elements.get('#resume').hidden, false);
  assert.match(elements.get('#status').textContent, /paused/);
  await vm.runInContext('resumeAudio()', context);
  assert.equal(elements.get('#resume').hidden, true);
  assert.match(elements.get('#status').textContent, /Listening/);
  track.muted = true;
  track.onmute();
  assert.match(elements.get('#status').textContent, /microphone is temporarily unavailable/);
  track.muted = false;
  track.onunmute();
  assert.match(elements.get('#status').textContent, /Listening/);
  track.onended();
  assert.match(elements.get('#status').textContent, /Microphone disconnected/);
  assert.equal(elements.get('#start').disabled, false);
  assert.equal(context.liveSocket.readyState, 3);
});

for (const reason of ['NotAllowedError', 'NotReadableError', 'NotFoundError']) {
  test(`microphone ${reason} releases audio and restores call controls`, async () => {
    const { context, elements } = liveBrowser();
    context.navigator.mediaDevices.getUserMedia = async () => { throw { name: reason }; };
    await vm.runInContext('startCall()', context);
    assert.equal(elements.get('#start').disabled, false);
    assert.equal(elements.get('#end').disabled, true);
    assert.equal(vm.runInContext('audioContext', context), undefined);
    assert.match(elements.get('#status').textContent, /[Mm]icrophone/);
  });
}

test('insecure phone LAN addresses explain the HTTPS requirement before opening a microphone', async () => {
  const { context, elements, constraints } = liveBrowser();
  context.isSecureContext = false;
  await vm.runInContext('startCall()', context);
  assert.match(elements.get('#status').textContent, /HTTPS/);
  assert.equal(constraints(), undefined);
});

test('ending a call closes its socket and detaches stale callbacks before audio cleanup', async () => {
  const { context, elements } = browser();
  const sent = [];
  let closed = false;
  let stopped = false;
  context.oldSocket = {
    readyState: 1, onclose() {}, onmessage() {},
    send: message => sent.push(JSON.parse(message)), close: () => { closed = true; },
  };
  context.oldContext = { state: 'running', currentTime: 0, close: async () => {} };
  context.stream = { getTracks: () => [{ stop: () => { stopped = true; } }] };
  await vm.runInContext('socket = oldSocket; audioContext = oldContext; microphoneStream = stream; stopCall()', context);
  assert.deepEqual(sent, [{ type: 'session.end' }]);
  assert.equal(closed, true);
  assert.equal(stopped, true);
  assert.equal(context.oldSocket.onclose, null);
  assert.equal(context.oldSocket.onmessage, null);
  assert.equal(elements.get('#start').disabled, false);
  assert.equal(vm.runInContext('socket === undefined && audioContext === undefined', context), true);
});

test('failed audio cleanup still restores call controls and preserves error message', async () => {
  const { context, elements } = browser();
  context.failedContext = { state: 'running', currentTime: 0, close: async () => { throw Error('closed'); } };
  await vm.runInContext('audioContext = failedContext; setStatus("Connection interrupted", true); stopCall(false, true)', context);
  assert.equal(elements.get('#start').disabled, false);
  assert.equal(elements.get('#end').disabled, true);
  assert.equal(elements.get('#status').textContent, 'Connection interrupted');
});

function localBrowser() {
  const elements = new Map();
  const actions = [];
  let releaseTail;
  const context = vm.createContext({
    document: { getElementById: id => {
      if (!elements.has(id)) elements.set(id, {
        handlers: {}, classList: { add() {}, toggle() {}, remove() {} },
        addEventListener(event, handler) { this.handlers[event] = handler; },
        pause() { actions.push('pause'); },
      });
      return elements.get(id);
    } },
    sessionStorage: { getItem: () => '' },
    fetch: async () => ({ ok: true, json: async () => ({ speech_input: true }) }),
    setTimeout: (resolve, delay) => { assert.equal(delay, 350); releaseTail = resolve; },
    navigator: { mediaDevices: { getUserMedia: async constraints => {
      assert.equal(constraints.audio.echoCancellation, true);
      assert.equal(constraints.audio.noiseSuppression, true);
      actions.push('microphone');
      return { getTracks: () => [{ stop: () => actions.push('release') }] };
    } } },
    MediaRecorder: class {
      start() { this.state = 'recording'; actions.push('record'); }
      stop() { this.state = 'inactive'; }
    },
    Blob,
  });
  vm.runInContext(readFileSync(new URL('src/preauth/local/web/app.js', root), 'utf8'), context);
  vm.runInContext('conversationId = "test"', context);
  return { context, elements, actions, releaseTail: () => releaseTail() };
}

test('local recording pauses the speaker, waits out echo and prevents playback while recording', async () => {
  const { context, elements, actions, releaseTail } = localBrowser();
  const recording = elements.get('mic').handlers.click();
  assert.deepEqual(actions, ['pause']);
  await elements.get('mic').handlers.click();
  assert.deepEqual(actions, ['pause'], 'double click cannot open a second microphone');
  elements.get('player').handlers.play();
  assert.deepEqual(actions, ['pause', 'pause']);
  releaseTail();
  await recording;
  assert.deepEqual(actions, ['pause', 'pause', 'microphone', 'record']);
  elements.get('player').handlers.play();
  assert.equal(actions.at(-1), 'pause');
});

for (const failure of ['constructor', 'start', 'error']) {
  test(`local recorder ${failure} failure releases microphone and restores controls`, async () => {
    const { context, elements, actions, releaseTail } = localBrowser();
    const BaseRecorder = context.MediaRecorder;
    context.MediaRecorder = class extends BaseRecorder {
      constructor() {
        super();
        if (failure === 'constructor') throw Error('Recorder unavailable');
      }
      start() {
        if (failure === 'start') throw Error('Cannot start recording');
        super.start();
      }
    };
    const recording = elements.get('mic').handlers.click();
    releaseTail();
    await recording;
    if (failure === 'error') vm.runInContext('recorder.onerror()', context);
    assert.equal(actions.filter(action => action === 'release').length, 1);
    assert.equal(elements.get('start').disabled, false);
    assert.equal(vm.runInContext('recorder', context), null);
  });
}
