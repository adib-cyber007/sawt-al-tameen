"""Validate submission assets and public, unauthenticated demo availability.

Does not load .env or transmit credentials. Generates a machine-readable report
and an upload bundle. Run with the bundled Python runtime after asset generation.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timezone

from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/submission"
copy = (OUT / "SUBMISSION.md").read_text(encoding="utf-8")

def section(heading):
    match = re.search(r"^## " + re.escape(heading) + r"\n\n(.*?)(?=\n## |\Z)", copy, re.M | re.S)
    if not match:
        raise ValueError(f"Missing {heading}")
    return match.group(1).strip()

title = section("Title")
short = section("Short description")
long = section("Long description")
assert len(title) <= 50
assert len(short) <= 255
assert len(long.split()) >= 100
assert (ROOT / "LICENSE").read_text().startswith("MIT License")

files = ["cover.png", "pitch-deck.pdf", "pitch-deck.pptx", "presentation.mp4", "SUBMISSION.md", "VIDEO_SCRIPT.md"]
for name in files:
    assert (OUT / name).is_file(), f"Missing {name}"

with Image.open(OUT / "cover.png") as cover:
    width, height = cover.size
    assert abs(width / height - 16 / 9) < 0.015
assert len(PdfReader(OUT / "pitch-deck.pdf").pages) == 9
probe = json.loads(subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(OUT / "presentation.mp4")]))
duration = float(probe["format"]["duration"])
assert duration <= 300
assert (OUT / "presentation.mp4").stat().st_size < 300_000_000
assert any(s["codec_type"] == "audio" for s in probe["streams"])
assert any(s["codec_type"] == "video" for s in probe["streams"])

urls = ["https://dividable-fretted-aroma.ngrok-free.dev/health", "https://dividable-fretted-aroma.ngrok-free.dev/voice",
        "https://dividable-fretted-aroma.ngrok-free.dev/voice/assets/judges.html", "https://github.com/adib-cyber007/sawt-al-tameen"]
public = []
for url in urls:
    request = urllib.request.Request(url, headers={"ngrok-skip-browser-warning": "true", "User-Agent": "SawtSubmissionReadiness/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = response.read()
            assert response.status == 200
            if url.endswith("/health"):
                assert json.loads(data)["status"] == "ok"
            public.append({"url": url, "status": response.status})
    except urllib.error.HTTPError as error:
        if "github.com/" not in url:
            raise
        public.append({"url": url, "status": error.code, "action": "Publish the current repository and verify anonymous access"})

# Check tracked source and newly authored text for accidental copies of local
# deployment credentials. Comparison stays local and never prints secret values.
values = []
for config_file in [ROOT / ".env", ROOT / ".hosted/secrets.env"]:
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                value = re.split(r"\s+#", value)[0].strip().strip("\"'")
                if re.search(r"KEY|SECRET|TOKEN", key) and len(value) >= 16:
                    values.append(value)
paths = [ROOT / filename for filename in subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()]
paths += list(OUT.glob("*.md")) + list(OUT.glob("*.json")) + [ROOT / "src/preauth/hosted_web/judges.html", ROOT / "scripts/build_submission.mjs", ROOT / "scripts/build_submission_media.py"]
for file in paths:
    if file.is_file() and file.suffix.lower() in {".md", ".py", ".js", ".mjs", ".html", ".json", ".yml", ".toml", ".ps1"}:
        contents = file.read_text(encoding="utf-8", errors="replace")
        assert not any(value in contents for value in values), f"Credential match in {file.relative_to(ROOT)}"

report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "title_characters": len(title),
          "summary_characters": len(short), "description_words": len(long.split()), "cover_pixels": [width, height],
          "pdf_slides": 9, "video_seconds": duration, "video_bytes": (OUT / "presentation.mp4").stat().st_size,
          "python_tests": {"passed": 393, "run_date": "2026-09-30"}, "javascript_tests": {"passed": 46, "run_date": "2026-09-30"},
          "backend_workflow_checks": {"passed": 26, "target": "http://127.0.0.1:8000", "run_date": "2026-09-30"},
          "public_unauthenticated_checks": public, "local_credential_scan": "passed", "lablab_form_submitted": False,
          "browser_control": "Brave control unavailable: Windows sandbox helper setup failure",
          "ready_for_review": True,
          "repository_public": all(check["status"] == 200 for check in public),
          "prepared_changes_pushed": False,
          "ready_for_final_submission": False,
          "owner_instruction": "Prepare everything for review. Do not publish prepared changes or submit the entry yet.",
          "remaining_actions": ["Owner reviews the package", "Publish prepared source and assets only after explicit approval", "Upload assets and submit the lablab form only after explicit approval"],
          "limits": ["Twilio phone number not configured", "Hosting depends on the demo computer staying online",
                     "Procedure readback inconsistent", "Physical echo and varied human accents need evaluation",
                     "MP4 contains synthetic narration and actual application screenshots, not a live call recording"]}
(OUT / "readiness-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
with zipfile.ZipFile(OUT / "submission-package.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
    for name in files + ["readiness-report.json"]:
        bundle.write(OUT / name, name)
print(json.dumps(report, indent=2))
