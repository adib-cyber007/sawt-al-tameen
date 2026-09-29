/* Local pre-authorisation console.
 *
 * This page is a telephone handset, nothing more. It records audio, posts turns and renders what comes back.
 * It holds no insurance logic and makes no judgements: every case reference, outcome, escalation and review
 * status shown here arrived from the backend in a response body.
 *
 * No secret is stored in this file. If the deployment sets PREAUTH_GATEWAY_SECRET, the page asks for it once
 * and keeps it in sessionStorage for this tab only.
 */
const API = "/api/v1/local";
const $ = (id) => document.getElementById(id);

const ui = {
  transcript: $("transcript"), input: $("input"), send: $("send"), mic: $("mic"),
  start: $("start"), finish: $("finish"), status: $("status"), engines: $("engines"),
  player: $("player"), composer: $("composer"),
};

let conversationId = null;
let recorder = null;
let busy = false;
let microphoneStarting = false;

// The local console also uses the loudspeaker. Never play a reply into an
// active recording, including when the native audio controls are used.
ui.player.addEventListener("play", () => {
  if (microphoneStarting || recorder?.state === "recording") ui.player.pause();
});

/* ------------------------------------------------------------------ transport */

function secret() {
  return sessionStorage.getItem("preauth_gateway_secret") || "";
}

async function call(path, { method = "GET", json, body, type } = {}) {
  const headers = {};
  if (secret()) headers["X-Gateway-Secret"] = secret();
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (type) headers["Content-Type"] = type;
  const response = await fetch(API + path, {
    method, headers, body: json !== undefined ? JSON.stringify(json) : body,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || { code: response.status, message: response.statusText };
    if (error.code === "GATEWAY_SECRET_INVALID") {
      const entered = window.prompt("This deployment requires X-Gateway-Secret:");
      if (entered) {
        sessionStorage.setItem("preauth_gateway_secret", entered);
        return call(path, { method, json, body, type });
      }
    }
    throw error;
  }
  return payload;
}

/* ------------------------------------------------------------------ rendering */

function addLine(role, text, extra = {}) {
  const line = document.createElement("div");
  line.className = `line ${role}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = extra.label || role;
  const body = document.createElement("span");
  body.className = "text" + (extra.cls ? " " + extra.cls : "");
  body.textContent = text;
  line.append(who, body);
  ui.transcript.append(line);
  ui.transcript.scrollTop = ui.transcript.scrollHeight;
}

function setStatus(text, isError = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle("error", isError);
}

function renderState(state) {
  $("s-conversation").textContent = state.conversation_id || "—";
  const verified = $("s-verified");
  verified.textContent = state.member_verified ? "caller and member"
    : state.verified ? "organisation only — member not yet verified" : "no";
  verified.className = state.member_verified ? "yes" : "pending";
  $("s-case").textContent = state.case_reference || "—";
  $("s-status").textContent = state.case_status || "—";
  $("s-outcome").textContent = state.recommendation_outcome
    ? `${state.recommendation_outcome} (advisory)` : "—";
  $("s-escalation").textContent = state.escalation_rule_ids.length
    ? state.escalation_rule_ids.join(", ") : "—";
  const review = $("s-review");
  review.textContent = (state.human_review_status || "—").replaceAll("_", " ").toLowerCase();
  review.className = state.human_review_status === "AWAITING_HUMAN_REVIEWER" ? "pending" : "";

  const intake = $("s-intake");
  intake.replaceChildren();
  const fields = [...Object.keys(state.intake), ...state.missing_intake_fields];
  if (!fields.length) intake.append(item("nothing collected yet", "muted"));
  for (const name of Object.keys(state.intake)) {
    intake.append(item(`${name.replaceAll("_", " ")}: ${state.intake[name]}`, "done"));
  }
  for (const name of state.missing_intake_fields) {
    intake.append(item(`${name.replaceAll("_", " ")} — still needed`, "todo"));
  }

  const documents = $("s-documents");
  documents.replaceChildren();
  if (!state.missing_documents.length) documents.append(item("—", "muted"));
  for (const doc of state.missing_documents) documents.append(item(doc, "todo"));
}

function item(text, cls) {
  const li = document.createElement("li");
  li.className = cls;
  li.textContent = text;
  return li;
}

function renderTools(calls) {
  const list = $("s-tools");
  if (!calls.length) return;
  if (list.querySelector(".muted")) list.replaceChildren();
  for (const toolCall of calls) {
    const label = toolCall.ok ? toolCall.tool : `${toolCall.tool} — ${toolCall.error_code}`;
    list.append(item(label, toolCall.ok ? "ok" : "failed"));
    addLine("tool", label, { label: "tool", cls: toolCall.ok ? "ok" : "failed" });
  }
}

function play(payload) {
  if (!payload.audio) {
    if (payload.speech_error) setStatus(payload.speech_error.message, true);
    return;
  }
  ui.player.src = `data:${payload.audio_media_type};base64,${payload.audio}`;
  ui.player.play().catch(() => {/* autoplay blocked until the user interacts; the text is already shown */});
}

function handle(payload) {
  if (payload.heard) addLine("caller", payload.heard, { label: "caller" });
  renderTools(payload.tool_calls || []);
  if (payload.decision_language_blocked) {
    addLine("system", "A final-decision statement was removed from the reply before it was spoken.",
      { label: "guard" });
  }
  addLine("agent", payload.reply, { label: "agent" });
  renderState(payload.state);
  play(payload);
}

/* ------------------------------------------------------------------ actions */

function setBusy(on, note = "") {
  busy = on;
  ui.send.disabled = on || !conversationId;
  ui.input.disabled = on || !conversationId;
  ui.mic.disabled = on || !conversationId || !engines.speech_input;
  ui.finish.disabled = on || !conversationId;
  ui.start.disabled = on;
  setStatus(note);
}

let engines = { speech_input: false, speech_output: false };

async function loadCapabilities() {
  try {
    engines = await call("/capabilities");
    ui.engines.textContent =
      `model ${engines.model}\nspeech in ${engines.transcriber} · out ${engines.synthesizer}`;
  } catch (error) {
    ui.engines.textContent = "local channel unavailable";
    setStatus(error.message || "The local channel is not enabled", true);
  }
}

ui.start.addEventListener("click", async () => {
  ui.transcript.replaceChildren();
  $("s-tools").replaceChildren(item("no tools called yet", "muted"));
  setBusy(true, "starting…");
  try {
    const payload = await call("/conversations", { method: "POST" });
    conversationId = payload.conversation_id;
    setBusy(false, "");
    handle(payload);
    ui.input.focus();
  } catch (error) {
    setBusy(false);
    setStatus(describe(error), true);
  }
});

ui.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = ui.input.value.trim();
  if (!text || busy) return;
  ui.input.value = "";
  addLine("caller", text, { label: "caller" });
  setBusy(true, "thinking…");
  try {
    const payload = await call(`/conversations/${conversationId}/text`, { method: "POST", json: { text } });
    delete payload.heard;  // already shown
    setBusy(false);
    handle(payload);
  } catch (error) {
    setBusy(false);
    setStatus(describe(error), true);
  }
});

ui.mic.addEventListener("click", async () => {
  if (microphoneStarting || !conversationId) return;
  if (recorder && recorder.state === "recording") {
    ui.mic.disabled = true;
    recorder.stop();
    return;
  }
  if (busy) return;
  microphoneStarting = true;
  const recordingConversation = conversationId;
  let stream;
  let currentRecorder;
  const releaseMicrophone = () => {
    if (currentRecorder) {
      currentRecorder.onstop = currentRecorder.ondataavailable = currentRecorder.onerror = null;
      if (currentRecorder.state === "recording") currentRecorder.stop();
    }
    stream?.getTracks().forEach((track) => track.stop());
    if (recorder === currentRecorder) recorder = null;
    ui.mic.classList.remove("recording");
    ui.mic.textContent = "🎙 Record";
  };
  try {
    if (globalThis.isSecureContext === false) throw new Error("Open the HTTPS site address to use the microphone on your PC or phone.");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      throw new Error("This browser cannot record audio. Please update your browser.");
    }
    ui.player.pause();
    setBusy(true, "preparing microphone…");
    await new Promise((resolve) => setTimeout(resolve, 350));
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    currentRecorder = recorder = new MediaRecorder(stream);
    const recordingChunks = [];
    currentRecorder.ondataavailable = (event) => {
      if (event.data.size) recordingChunks.push(event.data);
    };
    currentRecorder.onerror = () => {
      releaseMicrophone();
      setBusy(false, "Recording was interrupted. Please record your message again.");
    };
    currentRecorder.onstop = async () => {
      const mediaType = currentRecorder.mimeType || recordingChunks[0]?.type || "audio/webm";
      releaseMicrophone();
      const blob = new Blob(recordingChunks, { type: mediaType });
      if (!blob.size) {
        setBusy(false, "No audio was recorded. Please try again.");
        return;
      }
      setBusy(true, "transcribing…");
      try {
        const payload = await call(`/conversations/${recordingConversation}/audio`,
          { method: "POST", body: blob, type: blob.type });
        setBusy(false);
        handle(payload);
      } catch (error) {
        setBusy(false);
        setStatus(describe(error), true);
      }
    };
    recorder.start();
    ui.mic.disabled = false;
    ui.mic.classList.add("recording");
    ui.mic.textContent = "■ Stop";
    setStatus("recording…");
  } catch (error) {
    releaseMicrophone();
    setBusy(false);
    const message = error?.name === "NotAllowedError"
      ? "Microphone permission was denied. Allow access in your browser and device privacy settings, then try again."
      : error?.name === "NotReadableError"
        ? "The microphone could not be opened. Check whether another app is using it, then try again."
        : error?.message || "No microphone is available in this browser.";
    setStatus(message, true);
  } finally {
    microphoneStarting = false;
  }
});

ui.finish.addEventListener("click", async () => {
  setBusy(true, "storing transcript…");
  try {
    const payload = await call(`/conversations/${conversationId}/finish`, { method: "POST" });
    renderState(payload.state);
    addLine("system",
      `Call ended. Transcript stored as call record ${payload.call_record_id}; ` +
      `${payload.linked_case_ids.length} case(s) can now go to a human reviewer.`, { label: "system" });
    conversationId = null;
    setBusy(false, "call ended");
    ui.finish.disabled = true;
  } catch (error) {
    setBusy(false);
    setStatus(describe(error), true);
  }
});

function describe(error) {
  const fix = error.details && (error.details.start_it || error.details.pull_it || error.details.install);
  return fix ? `${error.message} — fix: ${fix}` : (error.message || "Something went wrong");
}

loadCapabilities();
