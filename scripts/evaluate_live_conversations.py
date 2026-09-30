"""Complete live synthetic caller scenarios against the hosted agent (Windows TTS).

Runs actual provider speech/replies and actual document transfer. Writes synthetic
transcripts and observed case outcomes under .hosted; never logs credentials.
Does not reproduce human accents, Brave audio devices or physical speaker echo.
Usage: python scripts/evaluate_live_conversations.py --base-url https://your-host
"""
import argparse
import asyncio
import base64
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import wave
from uuid import uuid4

from sqlalchemy import select
from websockets.asyncio.client import connect
from preauth.infrastructure.db.models import VoiceToolExecution
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.application.services import build_services
from preauth.application.assemblyai_post_call import AssemblyAIPostCallService
from preauth.infrastructure.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hosted_launcher", ROOT / "scripts/hosted.py")
hosted = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hosted)


def speech(text):
    path = ROOT / ".hosted" / ("caller-" + str(uuid4()) + ".wav")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    code = ("Add-Type -AssemblyName System.Speech; "
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, "
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
            "$synth.Rate = -2; $synth.SetOutputToWaveFile(" + quote(path) + ", $format); "
            "$synth.Speak(" + quote(text) + "); $synth.Dispose()")
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded], check=True, capture_output=True)
    try:
        with wave.open(str(path)) as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
                raise ValueError("Synthetic speech format mismatch")
            return wav.readframes(wav.getnframes())
    finally:
        path.unlink(missing_ok=True)


async def scenario(base_url, config, scenario_name):
    factory = build_session_factory(build_engine(config.get("PREAUTH_DATABASE_URL", "sqlite:///./preauth.db")))
    transcript = []
    checks = {}
    session_id = None
    progress = time.monotonic()
    done = asyncio.Event()
    ready = asyncio.Event()
    ending = False
    audio_bytes = 0
    queue = asyncio.Queue()
    treatment = (date.today() + timedelta(days=21)).isoformat()
    wrong = scenario_name == "verification_failure"
    identity = ("This is Aisha Rahman, provider staff at Al Hudaiba Crescent Hospital. "
                "Provider number P R V, three zero zero one one. Member policy P O L, S A, two zero two six, "
                "one hundred thousand and one, exactly six digits. Date of birth is " + ("the first of January nineteen ninety" if wrong else "the seventeenth of April nineteen eighty six") + ".")
    request = ("I request standard pre authorisation for knee arthroscopy, procedure S P, two zero zero four zero. "
               "Estimated cost twenty one thousand dirhams. Treatment date " + treatment + ".")
    report = {"scenario": scenario_name, "transcript": transcript, "checks": checks}
    url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/v1/voice/assemblyai/browser"

    def executions():
        if not session_id:
            return []
        with factory() as session:
            rows = session.scalars(select(VoiceToolExecution).where(VoiceToolExecution.conversation_id == session_id)
                                   .order_by(VoiceToolExecution.created_at)).all()
            return [{"tool": r.tool_name, "response": r.response} for r in rows if r.response]

    def upload(case_id, kind):
        headers = {"X-Gateway-Secret": config["PREAUTH_GATEWAY_SECRET"], "X-Actor-Type": "PROVIDER_PORTAL",
                   "X-Actor-Id": "synthetic-conversation-evaluation", "Content-Type": "application/pdf"}
        query = urllib.parse.urlencode({"document_type": kind, "title": "Synthetic conversation acceptance"})
        req = urllib.request.Request(base_url + f"/api/v1/cases/{case_id}/documents/upload?" + query,
                                     data=b"%PDF-1.4\nSynthetic evaluation only\n%%EOF", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as reply:
            return json.load(reply)

    async with connect(url, open_timeout=20) as socket:
        async def receive():
            nonlocal session_id, progress, audio_bytes
            async for raw in socket:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session.ready":
                    session_id = event["session_id"]; ready.set()
                if kind in {"reply.started", "reply.audio", "reply.done", "transcript.agent"}:
                    progress = time.monotonic()
                if kind == "reply.started":
                    done.clear()
                if kind == "reply.done":
                    done.set()
                if kind == "reply.audio":
                    audio_bytes += len(base64.b64decode(event.get("data", "")))
                if kind in {"transcript.agent", "transcript.user"}:
                    transcript.append({"role": "agent" if kind.endswith("agent") else "recognised_caller", "text": event.get("text", "")})
                if kind == "session.error":
                    raise RuntimeError("Provider error: " + str(event.get("code")))
            if not ending:
                raise RuntimeError("Voice connection closed unexpectedly")

        async def feed():
            await ready.wait()
            current, offset = b"", 0
            next_packet = time.monotonic()
            while not ending:
                if offset >= len(current) and not queue.empty():
                    current, offset = queue.get_nowait(), 0
                chunk = current[offset:offset+2400].ljust(2400, b"\0") if offset < len(current) else bytes(2400)
                offset += len(chunk)
                await socket.send(json.dumps({"type": "input.audio", "audio": base64.b64encode(chunk).decode()}))
                next_packet += .05
                await asyncio.sleep(max(0, next_packet-time.monotonic()))

        async def settle(timeout=65):
            start = time.monotonic()
            while time.monotonic()-start < timeout:
                if done.is_set() and time.monotonic()-progress > 3:
                    return
                if reader.done():
                    reader.result()
                await asyncio.sleep(.2)
            raise TimeoutError("Agent reply did not finish")

        reader = asyncio.create_task(receive()); sender = asyncio.create_task(feed())
        uploaded = False
        try:
            await asyncio.wait_for(ready.wait(), 25)
            await settle()
            for turn in range(12):
                rows = await asyncio.to_thread(executions)
                verifications = [(r["response"].get("result") or {}) for r in rows if r["tool"] == "verify_caller" and r["response"].get("ok")]
                coverage = [(r["response"].get("result") or {}) for r in rows if r["tool"] == "check_coverage_rule"]
                logged = any(r["tool"] == "log_transcript" and r["response"].get("ok") for r in rows)
                last_agent = next((t["text"] for t in reversed(transcript) if t["role"] == "agent"), "")
                confirm = bool(re.search(r"correct|confirm|right|accurate", last_agent, re.I))
                if logged and (wrong or any(c.get("outcome") == "RECOMMEND_APPROVAL" for c in coverage)):
                    break
                if not verifications:
                    utterance = "Yes, those details are correct. Please verify them." if confirm and not re.search(r"format|repeat|six digits", last_agent, re.I) else identity
                elif not verifications[-1].get("authorised") and not wrong:
                    utterance = "Please correct the member details and verify again. The policy is P O L dash S A dash twenty twenty six dash one hundred thousand and one. The last six digits are one, zero, zero, zero, zero, one. Date of birth is April seventeenth, nineteen eighty six."
                elif not verifications[-1].get("authorised"):
                    utterance = "Please arrange a callback for Aisha Rahman at plus nine seven one five zero one two three four five six seven. Please log the failed verification and end the call."
                elif not coverage:
                    utterance = "Yes, all details are correct. Please check coverage." if confirm else request
                elif coverage[-1].get("outcome") == "REQUEST_MORE_INFORMATION":
                    case = coverage[-1]
                    if not uploaded:
                        for kind in ("CLINICAL_NOTES", "OPERATIVE_PLAN", "PRIOR_TREATMENT_RECORD"):
                            await asyncio.to_thread(upload, case["case_id"], kind)
                        uploaded = True
                        checks["actual_documents_uploaded"] = True
                    utterance = "Yes, that is correct. Please recheck the same case, the requested documents are now uploaded." if confirm else "The requested clinical notes, operative plan and prior treatment record are now uploaded. Please recheck the same case. " + request
                else:
                    utterance = "Thank you. Please log the recommendation for human review and finish this call. I understand that a person must make the final decision."
                transcript.append({"role": "scripted_caller", "text": utterance})
                pcm = await asyncio.to_thread(speech, utterance)
                done.clear(); before = audio_bytes
                queue.put_nowait(pcm)
                await asyncio.sleep(len(pcm)/48000 + .5)
                await settle()
                report["session_id"] = session_id
                report["executions"] = await asyncio.to_thread(executions)
                (ROOT / ".hosted" / (scenario_name + "-running.json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({"scenario": scenario_name, "turn": turn+1, "tools": [r["tool"] for r in rows], "spoken_reply": audio_bytes > before}), flush=True)
                if audio_bytes <= before:
                    raise RuntimeError("Caller turn had no spoken agent reply")
            rows = await asyncio.to_thread(executions)
            report["session_id"] = session_id; report["executions"] = rows
            results = [(r["response"].get("result") or {}) for r in rows if r["tool"] == "check_coverage_rule"]
            verifies = [(r["response"].get("result") or {}) for r in rows if r["tool"] == "verify_caller" and r["response"].get("ok")]
            checks["verification_outcome_correct"] = bool(verifies) and bool(verifies[-1].get("authorised")) != wrong
            checks["call_summary_logged"] = any(r["tool"] == "log_transcript" and r["response"].get("ok") for r in rows)
            if wrong:
                checks["no_coverage_disclosure_tool"] = not results
            else:
                checks["documents_requested_before_recheck"] = any(r.get("outcome") == "REQUEST_MORE_INFORMATION" for r in results)
                checks["recommendation_pending_human_review"] = any(r.get("outcome") == "RECOMMEND_APPROVAL" and r.get("status") == "PENDING_HUMAN_REVIEW" and r.get("advisory_only") for r in results)
                checks["same_case_rechecked"] = len(results) >= 2 and len({r.get("case_id") for r in results}) == 1
            ending = True
            await socket.send(json.dumps({"type":"session.end"}))
        except Exception as exc:
            report["error"] = type(exc).__name__ + ": " + str(exc)
            ending = True
        finally:
            report["session_id"] = session_id
            report["executions"] = await asyncio.to_thread(executions)
            reader.cancel(); sender.cancel()
            await asyncio.gather(reader, sender, return_exceptions=True)
    if session_id:
        settings = Settings(database_url=config.get("PREAUTH_DATABASE_URL", "sqlite:///./preauth.db"),
                            assemblyai_api_key=config["ASSEMBLYAI_API_KEY"], assemblyai_webhook_secret=config["PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET"])
        services = build_services(factory)
        postcall = AssemblyAIPostCallService(settings, services.voice)
        for attempt in range(8):
            try:
                outcome = await asyncio.to_thread(postcall.ingest_session, session_id)
                checks["completed_transcript_persisted"] = outcome.accepted
                break
            except Exception as exc:
                if attempt == 7:
                    report["artifact_error"] = type(exc).__name__
                    checks["completed_transcript_persisted"] = False
                else:
                    await asyncio.sleep(5)
    report["passed"] = bool(checks) and all(checks.values()) and "error" not in report
    return report


async def main(args):
    config = hosted.load_config()
    results = []
    for name in args.scenarios:
        result = await scenario(args.base_url.rstrip("/"), config, name)
        results.append(result)
        Path(args.report).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps({"scenario": name, "passed": result["passed"], "checks": result["checks"], "error": result.get("error")}), flush=True)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenarios", nargs="+", choices=["document_recheck", "verification_failure"], default=["document_recheck", "verification_failure"])
    parser.add_argument("--report", default=".hosted/conversation-evaluation.json")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
