// Keep three seconds of speech ahead of playback to absorb measured two-second
// delivery gaps. Cap the initial buffering wait at five seconds once audio arrives.
class PcmPlaybackProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.inputRate = options?.processorOptions?.inputSampleRate || 24000;
    this.step = this.inputRate / sampleRate;
    this.replies = [];
    this.receiving = null;
    this.state = "idle";
    this.startSamples = Math.round(this.inputRate * 3);
    this.maxWaitFrames = Math.round(sampleRate * 5);
    this.port.onmessage = ({ data }) => {
      if (data.type === "audio") {
        const samples = new Int16Array(data.samples);
        if (!samples.length) return;
        if (!this.receiving) {
          this.receiving = { chunks: [], head: 0, offset: 0, phase: 0, buffered: 0,
            complete: false, playing: false, waitFrames: 0 };
          this.replies.push(this.receiving);
        }
        this.receiving.chunks.push(samples);
        this.receiving.buffered += samples.length;
      } else if (data.type === "flush") {
        if (this.receiving) this.receiving.complete = true;
        this.receiving = null;
      } else if (data.type === "clear") {
        this.replies = [];
        this.receiving = null;
      }
    };
  }

  get buffered() {
    return this.replies.reduce((count, reply) => count + reply.buffered, 0);
  }

  get replyActive() {
    return this.replies.length > 0;
  }

  setState(state) {
    if (state === this.state) return;
    this.state = state;
    this.port.postMessage({ type: "playback.state", state });
  }

  peek(reply, next = false) {
    const head = reply.chunks[reply.head];
    const index = reply.offset + (next ? 1 : 0);
    return index < head.length ? head[index] : (reply.chunks[reply.head + 1]?.[0] ?? head[head.length - 1]);
  }

  consume(reply) {
    reply.offset += 1;
    reply.buffered -= 1;
    if (reply.offset === reply.chunks[reply.head].length) {
      reply.chunks[reply.head++] = null;
      reply.offset = 0;
    }
  }

  process(_inputs, outputs) {
    const channel = outputs[0]?.[0];
    if (!channel) return true;
    channel.fill(0);
    // Protect microphone capture throughout preparation, playback and the
    // last render quantum. Capture adds its own device/room echo tail.
    outputs[1]?.[0]?.fill(this.replyActive ? 1 : 0);
    for (let i = 0; i < channel.length; i += 1) {
      const reply = this.replies[0];
      if (!reply) break;
      if (!reply.buffered && reply.complete) {
        this.replies.shift();
        i -= 1;
        continue;
      }
      if (!reply.playing) {
        if (reply.buffered && (reply.complete || reply.buffered >= this.startSamples
            || reply.waitFrames >= this.maxWaitFrames)) {
          reply.playing = true;
          reply.waitFrames = 0;
        } else {
          reply.waitFrames += 1;
          continue;
        }
      }
      this.setState("speaking");
      const current = this.peek(reply);
      const next = this.peek(reply, true);
      channel[i] = (current + (next - current) * reply.phase) / 32768;
      reply.phase += this.step;
      while (reply.phase >= 1 && reply.buffered) {
        this.consume(reply);
        reply.phase -= 1;
      }
      if (!reply.buffered) {
        if (reply.complete) this.replies.shift();
        else reply.playing = false;
      }
    }
    const head = this.replies[0];
    this.setState(head ? (head.playing ? "speaking" : "buffering") : "idle");
    return true;
  }
}

registerProcessor("pcm-playback", PcmPlaybackProcessor);
