const startButton = document.querySelector("#start");
const endButton = document.querySelector("#end");
const resumeButton = document.querySelector("#resume");
const status = document.querySelector("#status");
const connection = document.querySelector("#connection");
const transcript = document.querySelector("#transcript");
const correctionForm = document.querySelector("#correction-form");
const correctionInput = document.querySelector("#correction");
const correctionSend = document.querySelector("#correction-send");
const correctionStatus = document.querySelector("#correction-status");
const interruptButton = document.querySelector("#interrupt");
const retryButton = document.querySelector("#retry");
const wordPreview = document.querySelector("#word-preview");

let socket;
let audioContext;
let microphoneStream;
let captureNode;
let playbackNode;
let ready = false;
const partialTurns = { caller: undefined, agent: undefined };
let agentPartialReplyId;
let agentPartialText = "";
let terminalError = false;
let captureBlocked = false;
let playbackState = "idle";
let callGeneration = 0;
let starting = false;
const transcriptItems = new Map();
let currentReplyId;
let mutedReplyId;
let healthTimer;
let lastPong = 0;
let lastCapture = 0;
let waitingSince = 0;
let lastReplyProgress = 0;
let callerSpeaking = false;
let recoveryRequested = false;
let connectionRecovering = false;
let lastMicSpeech = 0;
let lastRecognition = 0;

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
  } else if (connectionRecovering) {
    setStatus("Reconnecting the voice service — please pause briefly.");
  } else if (recoveryRequested && playbackState === "idle") {
    setStatus("The reply is delayed. Trying to continue this conversation…");
  } else if (ready) {
    setStatus(playbackState === "buffering" ? "Preparing the reply — you can interrupt."
      : playbackState === "speaking"
      ? "Assistant speaking — speak to interrupt."
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

function appendTurn(role, text, partial = false, itemId) {
  transcript.querySelector(".empty")?.remove();
  const key = itemId ? `${role}:${itemId}` : undefined;
  const existing = key ? transcriptItems.get(key) : partialTurns[role];
  if (existing) {
    existing.querySelector(".content").textContent = text;
    if (!partial) {
      existing.classList.remove("partial");
      if (partialTurns[role] === existing) partialTurns[role] = undefined;
    }
    transcript.scrollTop = transcript.scrollHeight;
    return;
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
  if (key) transcriptItems.set(key, turn);
  // Bound the lookup cache; older rendered transcript rows remain readable.
  if (transcriptItems.size > 500) transcriptItems.delete(transcriptItems.keys().next().value);
  if (partial) partialTurns[role] = turn;
  transcript.scrollTop = transcript.scrollHeight;
}

function appendAgentDelta(event) {
  const itemId = event.item_id || event.reply_id;
  agentPartialText = transcriptItems.get(`agent:${itemId}`)?.querySelector(".content").textContent || "";
  const word = event.delta;
  if (!word) return;
  const needsSpace = agentPartialText && !/\s$/.test(agentPartialText)
    && !/^\s|^[,.;:!?)}\]]/.test(word);
  agentPartialText += (needsSpace ? " " : "") + word;
  appendTurn("agent", agentPartialText, true, event.item_id || event.reply_id);
}

function interruptPlayback() {
  mutedReplyId = currentReplyId;
  clearPlayback();
  playbackState = "idle";
  setStatus("Listening — say your correction now, or type it below.");
}

function retryReply() {
  if (!ready || socket?.readyState !== WebSocket.OPEN || retryButton.disabled) return;
  recoveryRequested = true;
  retryButton.disabled = true;
  interruptPlayback();
  lastReplyProgress = Date.now();
  socket.send(JSON.stringify({ type: "reply.retry" }));
  updateAudioStatus();
}

function checkCallHealth() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const now = Date.now();
  if (now - lastPong > 25000) {
    failCall("The connection stopped responding. Your transcript is kept below; start a new call to reconnect.");
    return;
  }
  socket.send(JSON.stringify({ type: "connection.ping" }));
  if (!ready || !audioContext || audioContext.state !== "running") return;
  if (now - lastCapture > 6000) {
    resumeButton.hidden = false;
    setStatus("Microphone audio has stopped arriving. Tap Resume audio or reconnect your microphone.");
    return;
  }
  // A missing provider speech.stopped event must not leave recovery disabled forever.
  if (callerSpeaking && now - Math.max(lastMicSpeech, lastRecognition) > 8000) {
    callerSpeaking = false;
    waitingSince ||= now;
  }
  // Never mistake caller silence between turns, active speech, or queued playback for a hang.
  if (waitingSince && !callerSpeaking && playbackState !== "speaking"
      && now - Math.max(waitingSince, lastReplyProgress) > 20000) {
    retryButton.hidden = false;
    retryButton.disabled = false;
    if (!recoveryRequested) retryReply();
    else setStatus("The assistant has not replied. Tap Retry reply, or send a typed correction below.");
  }
}

function sendCorrection(event) {
  event.preventDefault();
  const text = correctionInput.value.trim();
  if (!text || !ready || socket?.readyState !== WebSocket.OPEN || correctionSend.disabled) return;
  correctionSend.disabled = true;
  correctionStatus.textContent = "Sending correction…";
  interruptPlayback();
  socket.send(JSON.stringify({ type: "conversation.correction", text }));
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
  wordPreview.textContent = "Connecting live captions…";
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
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true },
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
    await context.audioWorklet.addModule("/voice/assets/pcm-capture.js?v=20260930-1");
    if (generation !== callGeneration) return;
    await context.audioWorklet.addModule("/voice/assets/pcm-playback.js?v=20260930-1");
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
        allowBargeIn: true },
    });
    playbackNode.connect(captureNode, 1, 1);
    captureNode.onprocessorerror = playbackNode.onprocessorerror = () => {
      if (generation === callGeneration) failCall("Audio processing stopped. Please start a new call.");
    };
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    source.connect(captureNode).connect(silent).connect(audioContext.destination);
    captureNode.port.onmessage = ({ data }) => {
      if (generation !== callGeneration) return;
      lastCapture = Date.now();
      if (data.type === "capture.state") {
        captureBlocked = data.blocked;
        updateAudioStatus();
        return;
      }
      if (data.type === "capture.level") {
        if (data.speech) lastMicSpeech = Date.now();
        return;
      }
      if (ready && context.state === "running" && !microphone.muted && socket?.readyState === WebSocket.OPEN) {
        // Never let delayed microphone packets build an ever-growing backlog.
        if (socket.bufferedAmount > 256000) {
          failCall("The connection is too slow for live audio. Your transcript is kept below; please reconnect.");
          return;
        }
        socket.send(JSON.stringify({ type: "input.audio", audio: base64(data) }));
      }
    };

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${location.host}/api/v1/voice/assemblyai/browser`);
    socket.onopen = () => {
      lastPong = lastCapture = Date.now();
      healthTimer = setInterval(checkCallHealth, 5000);
      setStatus("Connected. Initialising the assistant…");
      updateAudioStatus();
    };
    socket.onmessage = ({ data }) => {
      const event = JSON.parse(data);
      if (event.type === "session.ready") {
        ready = true;
        connectionRecovering = false;
        lastPong = Date.now();
        correctionInput.disabled = correctionSend.disabled = interruptButton.disabled = false;
        connection.textContent = "Live";
        connection.classList.add("live");
        endButton.disabled = false;
        updateAudioStatus();
      } else if (event.type === "caption.ready") {
        wordPreview.textContent = "Listening for your words…";
      } else if (event.type === "caption.preview") {
        wordPreview.textContent = event.text;
      } else if (event.type === "caption.unavailable") {
        wordPreview.textContent = "Fast preview is unavailable. The assistant’s own captions continue below.";
      } else if (event.type === "caption.reconnecting") {
        wordPreview.textContent = "Reconnecting fast captions. The conversation continues below.";
      } else if (event.type === "connection.pong") {
        lastPong = Date.now();
      } else if (event.type === "connection.reconnecting") {
        ready = false;
        connectionRecovering = true;
        clearPlayback();
        updateAudioStatus();
      } else if (event.type === "input.speech.started") {
        callerSpeaking = true;
        lastRecognition = Date.now();
        recoveryRequested = false;
      } else if (event.type === "input.speech.stopped") {
        callerSpeaking = false;
        waitingSince = Date.now();
      } else if (event.type === "reply.started") {
        currentReplyId = event.reply_id;
        waitingSince = lastReplyProgress = Date.now();
      } else if (event.type === "reply.audio" && event.data) {
        lastReplyProgress = Date.now();
        recoveryRequested = false;
        retryButton.hidden = true;
        if (!mutedReplyId || currentReplyId !== mutedReplyId) playPcm(event.data);
      } else if (event.type === "reply.done") {
        if (event.reply_id && currentReplyId && event.reply_id !== currentReplyId) return;
        waitingSince = event.status === "interrupted" ? Date.now() : 0;
        playbackNode?.port.postMessage({ type: event.status === "interrupted" ? "clear" : "flush" });
      } else if (event.type === "transcript.user.delta" && event.text) {
        lastRecognition = Date.now();
        appendTurn("caller", event.text, true, event.item_id);
      } else if (event.type === "transcript.user" && event.text) {
        callerSpeaking = false;
        waitingSince = Date.now();
        appendTurn("caller", event.text, false, event.item_id);
      } else if (event.type === "transcript.agent.delta" && event.delta) {
        appendAgentDelta(event);
      } else if (event.type === "transcript.agent" && event.text) {
        appendTurn("agent", event.text, false, event.item_id || event.reply_id);
        agentPartialReplyId = undefined;
        agentPartialText = "";
      } else if (event.type === "correction.accepted") {
        appendTurn("caller", `Correction: ${event.text}`, false, `correction-${Date.now()}`);
        correctionInput.value = "";
        correctionSend.disabled = false;
        correctionStatus.textContent = "Correction saved and sent. If the assistant does not confirm it, interrupt and say the detail again.";
        callerSpeaking = false;
        waitingSince = Date.now();
        recoveryRequested = false;
      } else if (event.type === "correction.error") {
        correctionSend.disabled = false;
        correctionStatus.textContent = event.message;
      } else if (event.type === "reply.retrying") {
        waitingSince = lastReplyProgress = Date.now();
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
  clearInterval(healthTimer);
  healthTimer = undefined;
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
  waitingSince = 0;
  callerSpeaking = recoveryRequested = connectionRecovering = false;
  currentReplyId = mutedReplyId = undefined;
  transcriptItems.clear();
  correctionInput.disabled = correctionSend.disabled = interruptButton.disabled = true;
  retryButton.hidden = true;
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
  partialTurns.caller = partialTurns.agent = undefined;
  agentPartialReplyId = undefined;
  agentPartialText = "";
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
interruptButton.addEventListener("click", interruptPlayback);
retryButton.addEventListener("click", retryReply);
correctionForm.addEventListener("submit", sendCorrection);
