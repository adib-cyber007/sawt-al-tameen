const startButton = document.querySelector("#start");
const endButton = document.querySelector("#end");
const status = document.querySelector("#status");
const connection = document.querySelector("#connection");
const transcript = document.querySelector("#transcript");

let socket;
let audioContext;
let microphoneStream;
let captureNode;
let ready = false;
let partialTurn;
let playbackAt = 0;
const playing = new Set();

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
  for (const source of playing) {
    try { source.stop(); } catch (_) { /* already stopped */ }
  }
  playing.clear();
  playbackAt = audioContext ? audioContext.currentTime : 0;
}

function playPcm(encoded) {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const samples = new Int16Array(bytes.buffer);
  const floats = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) floats[i] = samples[i] / 32768;
  const buffer = audioContext.createBuffer(1, floats.length, 24000);
  buffer.copyToChannel(floats, 0);
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  playbackAt = Math.max(playbackAt, audioContext.currentTime + .02);
  source.start(playbackAt);
  playbackAt += buffer.duration;
  playing.add(source);
  source.onended = () => playing.delete(source);
}

async function startCall() {
  startButton.disabled = true;
  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    audioContext = new AudioContext({ sampleRate: 24000 });
    await audioContext.audioWorklet.addModule("/voice/assets/pcm-capture.js");
    const source = audioContext.createMediaStreamSource(microphoneStream);
    captureNode = new AudioWorkletNode(audioContext, "pcm-capture");
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    source.connect(captureNode).connect(silent).connect(audioContext.destination);
    captureNode.port.onmessage = ({ data }) => {
      if (ready && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input.audio", audio: base64(data) }));
      }
    };

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${location.host}/api/v1/voice/assemblyai/browser`);
    socket.onopen = () => setStatus("Connected. Initialising the assistant…");
    socket.onmessage = ({ data }) => {
      const event = JSON.parse(data);
      if (event.type === "session.ready") {
        ready = true;
        connection.textContent = "Live";
        connection.classList.add("live");
        endButton.disabled = false;
        setStatus("Listening — speak naturally in English.");
      } else if (event.type === "reply.audio" && event.data) {
        playPcm(event.data);
      } else if (event.type === "input.speech.started") {
        clearPlayback();
      } else if (event.type === "transcript.user.delta" && event.text) {
        appendTurn("caller", event.text, true);
      } else if (event.type === "transcript.user" && event.text) {
        appendTurn("caller", event.text);
      } else if (event.type === "transcript.agent" && event.text) {
        appendTurn("agent", event.text);
      } else if (event.type === "session.error") {
        setStatus("The voice service reported an error. Please end the call and try again.", true);
      } else if (event.type === "session.ended") {
        stopCall(false);
      }
    };
    socket.onerror = () => setStatus("The voice service could not be reached.", true);
    socket.onclose = () => stopCall(false);
  } catch (error) {
    setStatus(error?.message || "Microphone access failed.", true);
    stopCall(false);
  }
}

async function stopCall(sendEnd = true) {
  if (sendEnd && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "session.end" }));
  }
  ready = false;
  captureNode?.disconnect();
  microphoneStream?.getTracks().forEach((track) => track.stop());
  clearPlayback();
  if (audioContext && audioContext.state !== "closed") await audioContext.close();
  if (!sendEnd && socket?.readyState === WebSocket.OPEN) socket.close();
  connection.textContent = "Not connected";
  connection.classList.remove("live");
  startButton.disabled = false;
  endButton.disabled = true;
  setStatus("Call ended.");
}

startButton.addEventListener("click", startCall);
endButton.addEventListener("click", () => stopCall(true));
