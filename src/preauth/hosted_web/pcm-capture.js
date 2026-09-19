class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (const value of channel) {
      const clamped = Math.max(-1, Math.min(1, value));
      this.samples.push(clamped < 0 ? clamped * 32768 : clamped * 32767);
    }
    if (this.samples.length >= 1200) {
      const pcm = new Int16Array(this.samples.splice(0, 1200));
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
