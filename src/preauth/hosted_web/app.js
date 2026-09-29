const startButton = document.querySelector("#start");
const endButton = document.querySelector("#end");
const resumeButton = document.querySelector("#resume");
const status = document.querySelector("#status");
const connection = document.querySelector("#connection");
const transcript = document.querySelector("#transcript");

let socket;
let audioContext;
let microphoneStream;
let captureNode;
let playbackNode;
let ready = false;
let partialTurn;
let terminalError = false;
let captureBlocked = false;
let playbackState = "idle";
let callGeneration = 0;
let starting = false;

function microphoneError(error) {
  const messages = {
    NotAllowedError: "Microphone permission was denied. Allow microphone access in this site's browser settings and your device privacy settings, then start again.",
    NotFoundError: "No microphone was found. Connect or enable a microphone, then start again.",
    NotReadableError: "The microphone could not be opened. Check whether another app is using it, then start again.",
  };
  return messages[error?.name] || error?.message || "Microphone access failed. Please start again.";
}

function updateAudioStatus() {
  if (!audioContext || terminalError) return;
  const paused = audioContext.state !== "running";
  resumeButton.hidden = !paused;
  if (paused) {
    setStatus("Audio is paused. Return to this page and tap Resume audio.");
  } else if (microphoneStream?.getAudioTracks()[0]?.muted) {
    setStatus("Your microphone is temporarily unavailable. Check your device or return from the other call.");
  } else if (ready) {
    setStatus(playbackState === "buffering" ? "Preparing the reply — please wait."
      : captureBlocked
      ? "Assistant speaking — please wait until Listening before speaking."
      : "Listening — speak naturally in English.");
  }
}

function failCall(message) {
  terminalError = true;
  setStatus(message, true);
  void stopCall(true, true);
}

async function resumeAudio() {
  const context = audioContext;
  if (!context) return;
  try {
    await context.resume();
    if (audioContext === context) updateAudioStatus();
  } catch {
    if (audioContext === context) failCall("Audio could not resume. Please start a new call.");
  }
}

function setStatus(text, error = false) {
  status.textContent = text;
  status.classList.toggle("error", error);
}

function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function appendTurn(role, text, partial = false) {
  transcript.querySelector(".empty")?.remove();
  if (partial && partialTurn) {
    partialTurn.querySelector(".content").textContent = text;
    return;
  }
  if (!partial && partialTurn) {
    partialTurn.remove();
    partialTurn = undefined;
  }
  const turn = document.createElement("p");
  turn.className = `turn ${role}${partial ? " partial" : ""}`;
  const speaker = document.createElement("span");
  speaker.className = "speaker";
  speaker.textContent = role === "caller" ? "You" : "Assistant";
  const content = document.createElement("span");
  content.className = "content";
  content.textContent = text;
  turn.append(speaker, content);
  transcript.append(turn);
  if (partial) partialTurn = turn;
  transcript.scrollTop = transcript.scrollHeight;
}

function clearPlayback() {
  playbackNode?.port.postMessage({ type: "clear" });
}

function playPcm(encoded) {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  playbackNode?.port.postMessage({ type: "audio", samples: bytes.buffer }, [bytes.buffer]);
}

async function startCall() {
  if (starting || audioContext) return;
  starting = true;
  const generation = ++callGeneration;
  startButton.disabled = true;
  endButton.disabled = false;
  terminalError = false;
  setStatus("Allow microphone access to start your call.");
  try {
    if (globalThis.isSecureContext === false) {
      throw new Error("Microphone access requires HTTPS. Open the secure site address on your PC or phone.");
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === "undefined") {
      throw new Error("This browser does not support voice calls. Open this page in an up-to-date browser.");
    }
    // Use the device rate so Firefox keeps acoustic echo cancellation and Safari does not silently send 48 kHz
    // samples as 24 kHz. The worklet converts the actual rate to AssemblyAI's required 24 kHz PCM.
    // Brave/Chromium's interactive mode uses the smallest hardware buffer it
    // can. Speech tolerates extra output latency and benefits from stability.
    audioContext = new AudioContext({ latencyHint: "playback" });
    const context = audioContext;
    context.onstatechange = () => {
      if (audioContext !== context) return;
      if (context.state === "closed") failCall("Audio stopped. Please start a new call.");
      else updateAudioStatus();
    };
    if (!context.audioWorklet) throw new Error("This browser cannot process voice audio. Please update your browser.");
    // Start audio inside the tap/click gesture, but do not let a pending resume
    // promise block the permission request or the End call button.
    void resumeAudio();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (generation !== callGeneration) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    microphoneStream = stream;
    const microphone = stream.getAudioTracks()[0];
    if (!microphone || microphone.readyState === "ended") throw new Error("No active microphone was found. Please reconnect it and start again.");
    microphone.onended = () => {
      if (generation === callGeneration) failCall("Microphone disconnected or permission removed. Reconnect or allow it, then start a new call.");
    };
    microphone.onmute = microphone.onunmute = () => {
      if (generation === callGeneration) updateAudioStatus();
    };
    await context.audioWorklet.addModule("/voice/assets/pcm-capture.js?v=20260928-2");
    if (generation !== callGeneration) return;
    await context.audioWorklet.addModule("/voice/assets/pcm-playback.js?v=20260928-2");
    if (generation !== callGeneration) return;
    playbackNode = new AudioWorkletNode(audioContext, "pcm-playback", {
      numberOfInputs: 0,
      numberOfOutputs: 2,
      outputChannelCount: [1, 1],
      processorOptions: { inputSampleRate: 24000 },
    });
    playbackNode.connect(audioContext.destination);
    playbackNode.port.onmessage = ({ data }) => {
      if (generation !== callGeneration || data.type !== "playback.state") return;
      playbackState = data.state;
      updateAudioStatus();
    };
    const source = audioContext.createMediaStreamSource(microphoneStream);
    captureNode = new AudioWorkletNode(audioContext, "pcm-capture", {
      numberOfInputs: 2,
      channelCount: 1,
      channelCountMode: "explicit",
      // Include the device output latency as well as room reverberation.
      processorOptions: { inputSampleRate: audioContext.sampleRate, targetSampleRate: 24000,
        echoTailSeconds: 0.35 + (audioContext.baseLatency || 0) + (audioContext.outputLatency || 0) },
    });
    playbackNode.connect(captureNode, 1, 1);
    captureNode.onprocessorerror = playbackNode.onprocessorerror = () => {
      if (generation === callGeneration) failCall("Audio processing stopped. Please start a new call.");
    };
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    source.connect(captureNode).connect(silent).connect(audioContext.destination);
    captureNode.port.onmessage = ({ data }) => {
      if (data.type === "capture.state") {
        captureBlocked = data.blocked;
        updateAudioStatus();
        return;
      }
      if (ready && context.state === "running" && !microphone.muted && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input.audio", audio: base64(data) }));
      }
    };

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${location.host}/api/v1/voice/assemblyai/browser`);
    socket.onopen = () => {
      setStatus("Connected. Initialising the assistant…");
      updateAudioStatus();
    };
    socket.onmessage = ({ data }) => {
      const event = JSON.parse(data);
      if (event.type === "session.ready") {
        ready = true;
        connection.textContent = "Live";
        connection.classList.add("live");
        endButton.disabled = false;
        updateAudioStatus();
      } else if (event.type === "reply.audio" && event.data) {
        playPcm(event.data);
      } else if (event.type === "reply.done") {
        playbackNode?.port.postMessage({ type: "flush" });
      } else if (event.type === "transcript.user.delta" && event.text) {
        appendTurn("caller", event.text, true);
      } else if (event.type === "transcript.user" && event.text) {
        appendTurn("caller", event.text);
      } else if (event.type === "transcript.agent" && event.text) {
        appendTurn("agent", event.text);
      } else if (event.type === "session.error") {
        failCall("The voice service reported an error. You can start a new call now.");
      } else if (event.type === "session.ended") {
        stopCall(false, terminalError);
      }
    };
    socket.onerror = () => {
      terminalError = true;
      setStatus("The voice service could not be reached. You can start a new call now.", true);
    };
    socket.onclose = ({ code }) => {
      if (code !== 1000 && code !== 1001) {
        terminalError = true;
        setStatus("The voice connection was interrupted. Please start a new call.", true);
      }
      stopCall(false, terminalError);
    };
    updateAudioStatus();
  } catch (error) {
    if (generation !== callGeneration) return;
    terminalError = true;
    setStatus(microphoneError(error), true);
    await stopCall(false, true);
  } finally {
    if (generation === callGeneration) starting = false;
  }
}

async function stopCall(sendEnd = true, preserveStatus = false) {
  callGeneration += 1;
  starting = false;
  // Detach the old connection before any await so its callbacks cannot tear down a later call.
  const closingSocket = socket;
  const closingContext = audioContext;
  socket = undefined;
  if (closingSocket) {
    closingSocket.onopen = closingSocket.onmessage = closingSocket.onerror = closingSocket.onclose = null;
    if (sendEnd && closingSocket.readyState === WebSocket.OPEN) {
      closingSocket.send(JSON.stringify({ type: "session.end" }));
    }
    if (closingSocket.readyState < WebSocket.CLOSING) closingSocket.close();
  }
  ready = false;
  captureBlocked = false;
  playbackState = "idle";
  if (playbackNode) playbackNode.port.onmessage = null;
  if (captureNode) captureNode.port.onmessage = null;
  captureNode?.disconnect();
  playbackNode?.disconnect();
  microphoneStream?.getTracks().forEach((track) => {
    track.onended = track.onmute = track.onunmute = null;
    track.stop();
  });
  if (closingContext) closingContext.onstatechange = null;
  clearPlayback();
  captureNode = playbackNode = microphoneStream = audioContext = undefined;
  partialTurn = undefined;
  connection.textContent = "Not connected";
  connection.classList.remove("live");
  startButton.disabled = false;
  endButton.disabled = true;
  resumeButton.hidden = true;
  if (!preserveStatus) setStatus("Call ended.");
  if (closingContext && closingContext.state !== "closed") {
    try { await closingContext.close(); } catch (_) { /* resources are already detached */ }
  }
}

startButton.addEventListener("click", startCall);
endButton.addEventListener("click", () => stopCall(true));
resumeButton.addEventListener("click", resumeAudio);
