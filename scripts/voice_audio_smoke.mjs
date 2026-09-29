#!/usr/bin/env node

import fs from "node:fs";

const [url, wavPath, timeoutText] = process.argv.slice(2);
if (!url || !wavPath) {
  console.error("Usage: node scripts/voice_audio_smoke.mjs <wss-url> <24khz-mono-pcm16.wav>");
  process.exit(2);
}

function wavPcm(buffer) {
  if (buffer.toString("ascii", 0, 4) !== "RIFF" || buffer.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("Input is not a RIFF/WAVE file");
  }
  let offset = 12;
  let format;
  let audio;
  while (offset + 8 <= buffer.length) {
    const name = buffer.toString("ascii", offset, offset + 4);
    const size = buffer.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (name === "fmt ") {
      format = {
        encoding: buffer.readUInt16LE(start),
        channels: buffer.readUInt16LE(start + 2),
        sampleRate: buffer.readUInt32LE(start + 4),
        bits: buffer.readUInt16LE(start + 14),
      };
    } else if (name === "data") {
      audio = buffer.subarray(start, start + size);
    }
    offset = start + size + (size % 2);
  }
  if (!format || !audio) throw new Error("WAV file has no format or audio chunk");
  if (format.encoding !== 1 || format.channels !== 1 || format.sampleRate !== 24000 || format.bits !== 16) {
    throw new Error(`Expected PCM16 mono 24 kHz; received ${JSON.stringify(format)}`);
  }
  return audio;
}

const speech = wavPcm(fs.readFileSync(wavPath));
const silence = Buffer.alloc(24000 * 2);
const input = Buffer.concat([silence.subarray(0, 7200 * 2), speech, silence]);
const socket = new WebSocket(url);
const started = Date.now();
let greetingDone = false;
let streaming = false;
let userText = "";
let agentText = "";
let audioChunks = 0;
let totalAudioBytes = 0;
let describedFirstAudio = false;
let timer;

socket.addEventListener("open", () => console.error("voice smoke: WebSocket open"));

function finish(error) {
  clearTimeout(timer);
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "session.end" }));
    socket.close();
  }
  if (error) {
    console.error(error.message || String(error));
    process.exitCode = 1;
    return;
  }
  console.log(JSON.stringify({
    user_transcript: userText,
    agent_transcript: agentText,
    agent_audio_chunks: audioChunks,
    total_audio_bytes: totalAudioBytes,
    elapsed_ms: Date.now() - started,
  }, null, 2));
}

async function streamAudio() {
  streaming = true;
  const bytesPerChunk = 1200 * 2;
  for (let offset = 0; offset < input.length; offset += bytesPerChunk) {
    if (socket.readyState !== WebSocket.OPEN) throw new Error("Socket closed while streaming audio");
    const chunk = input.subarray(offset, Math.min(offset + bytesPerChunk, input.length));
    socket.send(JSON.stringify({ type: "input.audio", audio: chunk.toString("base64") }));
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

socket.addEventListener("message", async ({ data }) => {
  try {
    const raw = data instanceof Blob
      ? await data.text()
      : data instanceof ArrayBuffer
        ? Buffer.from(data).toString("utf8")
        : String(data);
    const event = JSON.parse(raw);
    if (event.type !== "reply.audio") console.error(`voice smoke: ${event.type || "unknown event"}`);
    if (event.type === "session.error") {
      finish(new Error(`AssemblyAI session error: ${event.code || "unknown"}`));
    } else if (event.type === "reply.audio") {
      if (!describedFirstAudio) {
        describedFirstAudio = true;
        console.error(`voice smoke: first reply.audio keys=${Object.keys(event).join(",")} base64_chars=${event.data?.length || 0}`);
      }
      totalAudioBytes += event.data ? Buffer.from(event.data, "base64").length : 0;
      if (userText) audioChunks += 1;
    } else if (event.type === "transcript.user") {
      userText = event.text || "";
    } else if (event.type === "transcript.agent" && userText) {
      agentText = event.text || "";
    } else if (event.type === "reply.done" && !greetingDone) {
      greetingDone = true;
      await streamAudio();
    } else if (event.type === "reply.done" && userText && agentText) {
      finish();
    }
  } catch (error) {
    finish(error);
  }
});

socket.addEventListener("error", () => finish(new Error("WebSocket transport error")));
socket.addEventListener("close", () => {
  if (!agentText && !process.exitCode) finish(new Error("Socket closed before an agent reply"));
});

timer = setTimeout(() => {
  const phase = streaming ? "waiting for the user transcript and agent reply" : "waiting for the greeting";
  finish(new Error(`Timed out ${phase}; received ${totalAudioBytes} audio bytes`));
}, Number(timeoutText) || 60000);
