"""Export the reviewed slide renders to PDF and a narrated MP4.

Uses bundled ReportLab, installed Windows speech synthesis and FFmpeg. The
narration is synthetic. Application screenshots are from the real prototype.
Run after scripts/build_submission.mjs using the bundled Python runtime.
"""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import wave

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/submission"
BUILD = OUT / ".build"
narrations = json.loads((BUILD / "narrations.json").read_text(encoding="utf-8"))
pdf = canvas.Canvas(str(OUT / "pitch-deck.pdf"), pagesize=(960, 540))
pdf.setTitle("Sawt al-Tameen - AssemblyAI Voice Agent Hackathon")
pdf.setAuthor("Sawt al-Tameen contributors")
for index in range(1, len(narrations) + 1):
    pdf.drawImage(ImageReader(str(BUILD / f"slide-{index}.png")), 0, 0, width=960, height=540)
    pdf.showPage()
pdf.save()
print(f"PDF exported: {len(narrations)} slides", flush=True)

ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
if not ffmpeg or not ffprobe:
    raise RuntimeError("FFmpeg and FFprobe are required for the video")

def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"

clips = []
durations = []
for index, narration in enumerate(narrations, 1):
    wav = BUILD / f"narration-{index}.wav"
    code = (
        "Add-Type -AssemblyName System.Speech; "
        "$narrator = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$narrator.Rate = 0; "
        "$narrator.SetOutputToWaveFile(" + ps_quote(wav) + "); "
        "$narrator.Speak(" + ps_quote(narration) + "); $narrator.Dispose()"
    )
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded], check=True, capture_output=True)
    with wave.open(str(wav)) as audio:
        durations.append(audio.getnframes() / audio.getframerate())
    clip = BUILD / f"clip-{index}.mp4"
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(BUILD / f"slide-{index}.png"),
        "-i", str(wav), "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage", "-crf", "23",
        "-r", "12", "-vf", "scale=1920:1080,format=yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart", str(clip),
    ], check=True)
    clips.append(clip)
    print(f"Narrated slide {index}: {durations[-1]:.1f}s", flush=True)

listing = BUILD / "clips.txt"
listing.write_text("\n".join("file '" + str(clip).replace("\\", "/") + "'" for clip in clips), encoding="utf-8")
video = OUT / "presentation.mp4"
subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy", "-movflags", "+faststart", str(video)], check=True)
probe = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)]))
duration = float(probe["format"]["duration"])
size = video.stat().st_size
if duration > 300 or size > 300_000_000:
    raise RuntimeError("Video exceeds the lablab general guide limits")
report = {"slides": len(narrations), "video_seconds": duration, "video_bytes": size,
          "narration": "synthetic Windows speech", "video_type": "narrated pitch with actual application screenshots",
          "is_live_call_recording": False, "slide_seconds": durations}
(BUILD / "media-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
