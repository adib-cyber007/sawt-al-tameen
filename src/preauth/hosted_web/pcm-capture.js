class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const configured = options?.processorOptions || {};
    this.inputRate = configured.inputSampleRate || sampleRate;
    this.targetRate = configured.targetSampleRate || 24000;
    this.ratio = this.inputRate / this.targetRate;
    this.input = new Float32Array(0);
    this.position = 0;
    this.samples = new Int16Array(1200);
    this.sampleCount = 0;
    this.echoTailSamples = Math.ceil(this.inputRate * (configured.echoTailSeconds ?? 0.35));
    this.holdSamples = 0;
    this.blocked = false;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    const gate = inputs[1]?.[0];
    const protectedChannel = new Float32Array(channel.length);
    for (let i = 0; i < channel.length; i += 1) {
      if (gate?.[i] > 0) this.holdSamples = this.echoTailSamples;
      const blocked = gate?.[i] > 0 || this.holdSamples > 0;
      if (blocked !== this.blocked) {
        this.blocked = blocked;
        this.port.postMessage({ type: "capture.state", blocked });
        if (blocked) {
          // Do not release an old partial packet or interpolation sample after
          // playback begins. Continue streaming timed silence for provider VAD.
          this.samples.fill(0);
          this.input.fill(0);
        }
      }
      protectedChannel[i] = blocked ? 0 : channel[i];
      if (!(gate?.[i] > 0) && this.holdSamples > 0) this.holdSamples -= 1;
    }
    const combined = new Float32Array(this.input.length + channel.length);
    combined.set(this.input);
    combined.set(protectedChannel, this.input.length);

    while (this.position + 1 < combined.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const value = combined[left] + (combined[left + 1] - combined[left]) * fraction;
      const clamped = Math.max(-1, Math.min(1, value));
      this.samples[this.sampleCount++] = Math.round(clamped < 0 ? clamped * 32768 : clamped * 32767);
      if (this.sampleCount === this.samples.length) {
        this.port.postMessage(this.samples.buffer, [this.samples.buffer]);
        this.samples = new Int16Array(1200);
        this.sampleCount = 0;
      }
      this.position += this.ratio;
    }

    const consumed = Math.min(Math.floor(this.position), combined.length);
    this.input = combined.slice(consumed);
    this.position -= consumed;

    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
