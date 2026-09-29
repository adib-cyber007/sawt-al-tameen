"""Bring the hosted voice deployment up with one command: ``./scripts/run_hosted.sh``.

Orchestration only. Every step calls something that already exists — the backend, ``verify_deployment.py``,
the AssemblyAI setup script, or one documented provider endpoint. Nothing here touches cases or rules.

    a. start the backend
    b. start the tunnel (ngrok static domain, or Cloudflare named tunnel), and wait until the public URL
       answers
    c. verify the deployment through the public URL; stop if anything fails
    d. create or update the AssemblyAI agents, tools and post-call subscriptions
    e. restart with the generated agent ids and re-verify
    f. check the Twilio inbound endpoint and print the one Twilio console setting it needs
    g. print what is live, then keep both processes running until Ctrl+C

Configuration comes from ``.env``; see ``.env.example``. Values this script generates (the gateway secret and
provider webhook/media secrets) are kept in ``.hosted/secrets.env``, which is git-ignored and is
never read by local mode.
"""

import argparse
import ctypes
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from preauth.infrastructure.assemblyai_client import AssemblyAIClient, AssemblyAIError

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
HOSTED_DIR = ROOT / ".hosted"
SECRETS_FILE = HOSTED_DIR / "secrets.env"
ASSEMBLYAI_STATE_FILE = ROOT / ".assemblyai-state.json"

ASSEMBLYAI_WEBHOOK_PATH = "/api/v1/voice/assemblyai/post-call"
TWILIO_INBOUND_PATH = "/api/v1/voice/twilio/inbound"
_PLACEHOLDER = re.compile(r"^(|<.*>|your[-_ ].*|changeme|x+|\.\.\.)$", re.IGNORECASE)
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_IS_WINDOWS = os.name == "nt"
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def _on_ac_power() -> bool:
    if not _IS_WINDOWS:
        return False
    status = _SystemPowerStatus()
    return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))) and status.ACLineStatus == 1


def _set_execution_state(flags: int) -> bool:
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


def maintain_awake_on_ac(active: bool) -> bool:
    """Keep this launcher awake on AC; preserve normal battery and user-requested sleep behavior."""
    if not _IS_WINDOWS:
        return False
    desired = _on_ac_power()
    if desired == active:
        return active
    flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if desired else 0)
    if not _set_execution_state(flags):
        warn("Windows could not update the hosted service sleep request")
        return active
    info("automatic sleep inhibited while hosted service runs on AC" if desired else
         "normal sleep behavior restored on battery")
    return desired


class Stop(Exception):
    """A step failed in a way the operator must fix. Carries the explanation; never a stack trace."""


# --------------------------------------------------------------------------- output


def step(letter: str, title: str) -> None:
    print(f"\n\033[1m[{letter}] {title}\033[0m", flush=True)


def ok(text: str) -> None:
    print(f"    \033[32mPASS\033[0m  {text}", flush=True)


def info(text: str) -> None:
    print(f"          {text}", flush=True)


def warn(text: str) -> None:
    print(f"    \033[33mWARN\033[0m  {text}", flush=True)


def banner(text: str, colour: str) -> None:
    code = {"green": "32", "red": "31", "yellow": "33"}[colour]
    line = "=" * 78
    print(f"\n\033[{code}m{line}\n  {text}\n{line}\033[0m", flush=True)


# --------------------------------------------------------------------------- configuration


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Inline ``# comments`` after whitespace are dropped; quotes are stripped."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def load_config() -> dict[str, str]:
    """Precedence: the real environment, then .env, then values this script generated earlier."""
    config = read_env_file(SECRETS_FILE)
    config.update({k: v for k, v in read_env_file(ENV_FILE).items() if v})
    config.update({k: v for k, v in os.environ.items() if v})
    return config


def is_set(config: dict[str, str], name: str) -> bool:
    return not _PLACEHOLDER.match(config.get(name, "").strip())


def remember_secret(config: dict[str, str], name: str, value: str) -> None:
    HOSTED_DIR.mkdir(exist_ok=True)
    existing = read_env_file(SECRETS_FILE)
    existing[name] = value
    lines = ["# Generated by scripts/run_hosted.sh. Git-ignored. Delete a line to have it regenerated."]
    lines += [f"{k}={v}" for k, v in existing.items()]
    SECRETS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SECRETS_FILE.chmod(0o600)
    config[name] = value


def load_assemblyai_state() -> dict[str, Any]:
    return json.loads(ASSEMBLYAI_STATE_FILE.read_text(encoding="utf-8")) if ASSEMBLYAI_STATE_FILE.exists() else {}


# --------------------------------------------------------------------------- processes


def _stop_process(process: subprocess.Popen) -> None:
    """Stop one child process without assuming POSIX process-group APIs exist."""
    if process.poll() is not None:
        return
    if _IS_WINDOWS:
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if _IS_WINDOWS:
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)


class Processes:
    def __init__(self) -> None:
        self.children: dict[str, subprocess.Popen] = {}

    def start(self, name: str, command: list[str], env: dict[str, str]) -> subprocess.Popen:
        HOSTED_DIR.mkdir(exist_ok=True)
        log = open(HOSTED_DIR / f"{name}.log", "ab")
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        self.children[name] = process
        return process

    def stop(self, name: str) -> None:
        process = self.children.pop(name, None)
        if process:
            _stop_process(process)

    def stop_all(self) -> None:
        for name in list(self.children):
            self.stop(name)

    def exited(self) -> str | None:
        return next((n for n, p in self.children.items() if p.poll() is not None), None)


def log_tail(name: str, lines: int = 15) -> str:
    path = HOSTED_DIR / f"{name}.log"
    if not path.exists():
        return "(no log)"
    return "\n".join("          | " + l for l in path.read_text(errors="replace").splitlines()[-lines:])


def http_status(url: str, timeout: float = 5) -> tuple[int | None, str]:
    try:
        request = urllib.request.Request(url, headers={"ngrok-skip-browser-warning": "true"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(200).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, ""


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


# --------------------------------------------------------------------------- steps


TUNNELS = ("ngrok", "cloudflare")
NGROK_INSTALL = "https://ngrok.com/download"
CLOUDFLARED_INSTALL = (
    "https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/"
)


def choose_tunnel(config: dict[str, str], args: argparse.Namespace, problems: list[str]) -> str:
    """Pick the tunnel and settle the public URL. Nothing after preflight looks at anything but that URL.

    PREAUTH_TUNNEL_PROVIDER wins when set. Otherwise the provider whose credentials are present is used, ngrok
    first when both are (it needs no domain). Neither configured is an error that names both options.
    """
    if args.no_tunnel:
        if not is_set(config, "PREAUTH_PUBLIC_BASE_URL"):
            problems.append("--no-tunnel needs PREAUTH_PUBLIC_BASE_URL: the URL that already reaches this machine")
        return "none"

    has_ngrok = is_set(config, "NGROK_AUTHTOKEN") or is_set(config, "NGROK_STATIC_DOMAIN")
    has_cloudflare = is_set(config, "CLOUDFLARE_TUNNEL_TOKEN")
    chosen = (config.get("PREAUTH_TUNNEL_PROVIDER") or "").strip().lower()
    if chosen and chosen not in TUNNELS:
        problems.append(f"PREAUTH_TUNNEL_PROVIDER must be ngrok or cloudflare (got {chosen!r})")
        return chosen
    if not chosen:
        chosen = "ngrok" if has_ngrok else "cloudflare" if has_cloudflare else ""
    if not chosen:
        problems.append(
            "No tunnel is configured. Fill in ONE provider in the HOSTED MODE — tunnel section of .env:\n"
            "              ngrok (free, no domain needed): NGROK_AUTHTOKEN and NGROK_STATIC_DOMAIN\n"
            "              Cloudflare (needs a domain):     CLOUDFLARE_TUNNEL_TOKEN and PREAUTH_PUBLIC_BASE_URL"
        )
        return "none"

    if chosen == "ngrok":
        if not is_set(config, "NGROK_AUTHTOKEN"):
            problems.append("NGROK_AUTHTOKEN is empty — https://dashboard.ngrok.com/get-started/your-authtoken")
        domain = re.sub(r"^https?://", "", config.get("NGROK_STATIC_DOMAIN", "").strip()).strip("/")
        if not is_set(config, "NGROK_STATIC_DOMAIN"):
            problems.append("NGROK_STATIC_DOMAIN is empty — claim your free one at https://dashboard.ngrok.com/domains")
        elif "/" in domain or "." not in domain:
            problems.append(f"NGROK_STATIC_DOMAIN must be a bare hostname like name.ngrok-free.app (got {domain!r})")
        if shutil.which("ngrok") is None:
            problems.append(f"ngrok is not installed — {NGROK_INSTALL}")
        # The URL is the reserved domain; it is the same on every run, which is what the voice-agent tools and
        # webhook need.
        given = config.get("PREAUTH_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if domain and is_set(config, "PREAUTH_PUBLIC_BASE_URL") and given != f"https://{domain}":
            warn(f"PREAUTH_PUBLIC_BASE_URL ({given}) is ignored with ngrok; using https://{domain}")
        config["PREAUTH_PUBLIC_BASE_URL"] = f"https://{domain}"
        return "ngrok"

    if not is_set(config, "CLOUDFLARE_TUNNEL_TOKEN"):
        problems.append("CLOUDFLARE_TUNNEL_TOKEN is empty — see the ONE-TIME Cloudflare steps in .env.example")
    if shutil.which("cloudflared") is None:
        problems.append(f"cloudflared is not installed — {CLOUDFLARED_INSTALL}")
    base_url = config.get("PREAUTH_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not is_set(config, "PREAUTH_PUBLIC_BASE_URL"):
        problems.append("PREAUTH_PUBLIC_BASE_URL is empty — the https:// hostname you gave the Cloudflare tunnel")
    elif not base_url.startswith("https://"):
        problems.append(f"PREAUTH_PUBLIC_BASE_URL must start with https:// (got {base_url!r})")
    return "cloudflare"


def _redact(text: str, config: dict[str, str]) -> str:
    """ngrok repeats a rejected authtoken back in its error text; never print it."""
    token = config.get("NGROK_AUTHTOKEN", "")
    return text.replace(token, "<NGROK_AUTHTOKEN>") if token else text


def _ngrok_error(line: str) -> str | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    err = record.get("err") or ""
    if record.get("lvl") in ("eror", "crit") and ("ERR_NGROK_" in err or "authentication failed" in err):
        return err
    return None


def preflight(config: dict[str, str], args: argparse.Namespace) -> None:
    step("0", "Checking .env")
    if not ENV_FILE.exists():
        raise Stop("No .env file. Run:  cp .env.example .env  then fill in the HOSTED MODE section.")

    problems = []
    voice_provider = (config.get("VOICE_PROVIDER") or "assemblyai").strip().lower()
    if voice_provider != "assemblyai":
        problems.append(f"VOICE_PROVIDER must be assemblyai (got {voice_provider!r})")
    config["VOICE_PROVIDER"] = "assemblyai"

    for name in ("PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET", "PREAUTH_ASSEMBLYAI_MEDIA_SECRET"):
        if not is_set(config, name):
            remember_secret(config, name, secrets.token_urlsafe(32))
            info(f"generated {name} (kept in .hosted/secrets.env)")
    if not is_set(config, "ASSEMBLYAI_API_KEY"):
        problems.append("ASSEMBLYAI_API_KEY is empty — create one in the AssemblyAI dashboard")
    if not is_set(config, "PREAUTH_ASSEMBLYAI_VOICE_ID"):
        problems.append("PREAUTH_ASSEMBLYAI_VOICE_ID is empty — select an English Voice Agent voice")
    state = load_assemblyai_state().get("agents") or {}
    for channel, name in (
        ("browser", "PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID"),
        ("phone", "PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID"),
    ):
        if not is_set(config, name) and isinstance(state.get(channel), str):
            config[name] = state[channel]
    provider = choose_tunnel(config, args, problems)
    base_url = config.get("PREAUTH_PUBLIC_BASE_URL", "").strip().rstrip("/")

    if is_set(config, "TWILIO_PHONE_NUMBER") and not is_set(config, "TWILIO_AUTH_TOKEN"):
        problems.append("TWILIO_PHONE_NUMBER is set but TWILIO_AUTH_TOKEN is not; the backend needs the token to "
                        "verify Twilio's signatures (console.twilio.com → Account Info → Auth Token)")
    if is_set(config, "TWILIO_PHONE_NUMBER") and not _E164.match(config["TWILIO_PHONE_NUMBER"].replace(" ", "")):
        problems.append("TWILIO_PHONE_NUMBER must be in international format, e.g. +14155550123")

    if problems:
        raise Stop("Fix these in .env and run again:\n" + "\n".join(f"          - {p}" for p in problems))
    config["PREAUTH_PUBLIC_BASE_URL"] = base_url
    config["_TUNNEL"] = provider
    ok(f".env has everything this run needs (voice: {voice_provider}; tunnel: {provider})")

    # Fail fast on a bad key, before anything is started.
    try:
        AssemblyAIClient(
            config["ASSEMBLYAI_API_KEY"],
            api_base=config.get("PREAUTH_ASSEMBLYAI_API_BASE", "https://agents.assemblyai.com"),
        ).request("GET", "/v1/agents", query={"limit": 1})
    except AssemblyAIError as exc:
        raise Stop(f"AssemblyAI rejected or could not verify ASSEMBLYAI_API_KEY: {exc}") from None
    ok("AssemblyAI accepted the API key")

    for name in ("PREAUTH_VOICE_TOOL_TOKEN", "PREAUTH_GATEWAY_SECRET"):
        if not is_set(config, name):
            remember_secret(config, name, secrets.token_urlsafe(32))
            info(f"generated {name} (kept in .hosted/secrets.env)")

    port = int(config.get("PREAUTH_HOSTED_PORT") or 8000)
    if not port_free(port):
        raise Stop(
            f"Port {port} is in use. The tunnel forwards to a fixed port (the Service URL you set in Cloudflare),\n"
            f"          so this cannot move to another one automatically. Stop whatever is on {port}, or set\n"
            f"          PREAUTH_HOSTED_PORT in .env and change the tunnel's Service URL to match."
        )
    config["PREAUTH_HOSTED_PORT"] = str(port)


def backend_env(config: dict[str, str]) -> dict[str, str]:
    env = {**os.environ, **config}
    env["PREAUTH_RUNTIME_MODE"] = "hosted"
    env.pop("CLOUDFLARE_TUNNEL_TOKEN", None)
    env.pop("TUNNEL_TOKEN", None)
    env.pop("NGROK_AUTHTOKEN", None)
    return env


def start_backend(config: dict[str, str], processes: Processes) -> None:
    env = backend_env(config)
    for command in (
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        [sys.executable, "-m", "preauth.seed", "--if-empty"],
    ):
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise Stop(f"{' '.join(command[2:])} failed:\n{result.stdout}{result.stderr}")
    info("database migrated and catalogue present")

    port = config["PREAUTH_HOSTED_PORT"]
    processes.start(
        "backend",
        [sys.executable, "-m", "uvicorn", "preauth.main:app", "--host", "127.0.0.1", "--port", port,
         "--proxy-headers", "--forwarded-allow-ips=*"],
        env,
    )
    for _ in range(60):
        if http_status(f"http://127.0.0.1:{port}/health")[0] == 200:
            ok(f"backend is serving on http://127.0.0.1:{port} (log: .hosted/backend.log)")
            return
        if processes.exited() == "backend":
            break
        time.sleep(0.5)
    raise Stop(f"The backend did not start.\n{log_tail('backend')}")


def start_tunnel(config: dict[str, str], processes: Processes) -> None:
    if config["_TUNNEL"] == "ngrok":
        start_ngrok_tunnel(config, processes)
    else:
        start_cloudflare_tunnel(config, processes)


def start_ngrok_tunnel(config: dict[str, str], processes: Processes) -> None:
    public = config["PREAUTH_PUBLIC_BASE_URL"]
    launch_ngrok_tunnel(config, processes)
    info(f"waiting for {public} to answer (up to 60 s)")
    last = None
    for _ in range(30):
        log_path = HOSTED_DIR / "tunnel.log"
        for line in (log_path.read_text(errors="replace").splitlines() if log_path.exists() else []):
            error = _ngrok_error(line)
            if error:
                raise Stop("ngrok refused the tunnel.\n          ngrok said: " + _redact(error.strip(), config))
        if processes.exited() == "tunnel":
            raise Stop("ngrok exited.\n" + _redact(log_tail("tunnel"), config))
        last, body = http_status(f"{public}/health", timeout=8)
        if last == 200 and '"ok"' in body:
            ok(f"tunnel is up: {public} reaches this backend via ngrok (log: .hosted/tunnel.log)")
            return
        time.sleep(2)
    raise Stop(f"{public}/health never answered through ngrok (last: HTTP {last}).\n"
               + _redact(log_tail("tunnel"), config))


def launch_ngrok_tunnel(config: dict[str, str], processes: Processes) -> None:
    """Launch the reserved endpoint with an explicit HTTP upstream and loopback address."""
    env = {**os.environ, "NGROK_AUTHTOKEN": config["NGROK_AUTHTOKEN"]}  # via env, never on the command line
    processes.start(
        "tunnel",
        ["ngrok", "http", f"http://127.0.0.1:{config['PREAUTH_HOSTED_PORT']}",
         "--url", config["PREAUTH_PUBLIC_BASE_URL"], "--log", "stdout", "--log-format", "json"],
        env,
    )


def check_ngrok_health(config: dict[str, str], processes: Processes, failures: int) -> int:
    """Heal a persistent public-route failure without restarting the backend or changing the URL."""
    status, body = http_status(f"{config['PREAUTH_PUBLIC_BASE_URL']}/health", timeout=5)
    if status == 200 and '"ok"' in body:
        return 0
    failures += 1
    if failures < 3:
        return failures
    local_status, _ = http_status(f"http://127.0.0.1:{config['PREAUTH_HOSTED_PORT']}/health", timeout=3)
    if local_status != 200:
        warn("public health failed and the local backend is unhealthy; see .hosted/backend.log")
        return 0
    warn(f"public health failed three times (last HTTP {status}); restarting the ngrok tunnel")
    processes.stop("tunnel")
    launch_ngrok_tunnel(config, processes)
    return 0


def start_cloudflare_tunnel(config: dict[str, str], processes: Processes) -> None:
    env = {**os.environ, "TUNNEL_TOKEN": config["CLOUDFLARE_TUNNEL_TOKEN"]}  # via env, never on the command line
    processes.start("tunnel", ["cloudflared", "tunnel", "--no-autoupdate", "run"], env)
    public = config["PREAUTH_PUBLIC_BASE_URL"]
    info(f"waiting for {public} to answer (up to 90 s)")
    bad_token = (
        "Cloudflare rejected CLOUDFLARE_TUNNEL_TOKEN (cloudflared: \"Failed to get tunnel\"). The token is\n"
        "          wrong, truncated, or the tunnel was deleted. Copy it again: Cloudflare dashboard → Networking →\n"
        "          Tunnels → your tunnel → the token in the install command.\n"
    )
    last = None
    for _ in range(45):
        log = (HOSTED_DIR / "tunnel.log").read_text(errors="replace") if (HOSTED_DIR / "tunnel.log").exists() else ""
        # cloudflared does not exit on a bad token; it retries forever. Read its log instead of waiting it out.
        if log.count("Register tunnel error") >= 2 or "Unauthorized" in log or "Invalid tunnel secret" in log:
            raise Stop(bad_token + log_tail("tunnel", 4))
        if processes.exited() == "tunnel":
            raise Stop(f"cloudflared exited.\n{log_tail('tunnel')}")
        last, body = http_status(f"{public}/health", timeout=8)
        if last == 200 and '"ok"' in body:
            ok(f"tunnel is up: {public} reaches this backend (log: .hosted/tunnel.log)")
            return
        time.sleep(2)
    if "Registered tunnel connection" not in log:
        raise Stop(f"cloudflared never connected to Cloudflare in 90 s. Check the network.\n{log_tail('tunnel')}")
    hint = {
        502: "Cloudflare reached the tunnel but not the backend: the hostname's Service URL must be "
             f"http://localhost:{config['PREAUTH_HOSTED_PORT']}",
        404: "the hostname answers but is not routed to this tunnel — check the Public Hostname entry",
        530: "the hostname is not connected to a running tunnel — check the hostname belongs to THIS tunnel",
        None: "the hostname does not resolve or answer — check PREAUTH_PUBLIC_BASE_URL matches the hostname "
              "you published",
    }.get(last, f"got HTTP {last}")
    raise Stop(f"{public}/health never answered: {hint}.\n{log_tail('tunnel')}")


def verify(config: dict[str, str], label: str) -> None:
    print(f"          running scripts/verify_deployment.py against {config['PREAUTH_PUBLIC_BASE_URL']}\n")
    result = subprocess.run(
        [sys.executable, "scripts/verify_deployment.py", "--base-url", config["PREAUTH_PUBLIC_BASE_URL"]],
        cwd=ROOT, env=backend_env(config), capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    print("\n".join("      " + l for l in output.rstrip().splitlines()))
    counts = re.search(r"(\d+) passed, (\d+) failed", output)
    summary = f"{counts.group(1)} passed, {counts.group(2)} failed" if counts else "no summary line"
    if result.returncode != 0:
        failed = [l.strip() for l in output.splitlines() if l.strip().startswith("FAIL")]
        banner(f"VERIFY ({label}): FAIL — {summary}. Provider setup was not changed further.", "red")
        raise Stop("These checks failed:\n" + "\n".join(f"          {l}" for l in failed or [output[-400:]]))
    banner(f"VERIFY ({label}): PASS — {summary}", "green")


def setup_assemblyai(config: dict[str, str]) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, "scripts/assemblyai_setup.py"],
        cwd=ROOT,
        env=backend_env(config),
        capture_output=True,
        text=True,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith(("agent:", "webhook:")):
            info(line)
    if result.returncode != 0:
        raise Stop(f"assemblyai_setup.py failed:\n          {(result.stderr or result.stdout).strip()[-800:]}")
    agents = load_assemblyai_state().get("agents") or {}
    if not all(isinstance(agents.get(channel), str) and agents[channel] for channel in ("browser", "phone")):
        raise Stop("assemblyai_setup.py finished without recording both agent ids in .assemblyai-state.json")
    ok(
        f"agents {agents['browser']} (browser) and {agents['phone']} (phone): "
        "English prompt, 3 function tools, audio formats and completed-session webhooks"
    )
    return {"browser": agents["browser"], "phone": agents["phone"]}


def setup_provider(config: dict[str, str]) -> dict[str, str]:
    return setup_assemblyai(config)


def wire_phone_number(config: dict[str, str]) -> str | None:
    """Inbound calls reach AssemblyAI through our own signed Twilio endpoint.

    The backend returns TwiML for a signed local Media Stream; the bridge then opens the stored PCMU agent. This
    check never places a call.
    """
    inbound = config["PREAUTH_PUBLIC_BASE_URL"] + TWILIO_INBOUND_PATH
    # An unsigned probe: 401 means configured and enforcing signatures; 503 names what is missing. No call is made.
    status, body = _post_status(inbound)
    if status == 401:
        ok(f"inbound endpoint is live and verifying Twilio signatures: {inbound}")
    elif status == 503:
        missing = re.findall(
            r"TWILIO_AUTH_TOKEN|PREAUTH_PUBLIC_BASE_URL|ASSEMBLYAI_API_KEY|"
            r"PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID|PREAUTH_ASSEMBLYAI_MEDIA_SECRET",
            body,
        )
        warn(f"inbound calls are disabled until these are set: {', '.join(sorted(set(missing))) or 'see backend log'}")
    else:
        warn(f"the inbound endpoint answered HTTP {status}; check .hosted/backend.log")

    number = config.get("TWILIO_PHONE_NUMBER", "").replace(" ", "") if is_set(config, "TWILIO_PHONE_NUMBER") else None
    for line in (
        "Twilio setting (ONE-TIME, in the Twilio console):",
        f"  Phone Numbers → Manage → Active numbers → {number or 'your number'} → Voice Configuration →",
        "  A call comes in: Webhook   URL:",
        f"  {inbound}",
        "  HTTP: POST   → Save configuration",
        "  Trial accounts only accept calls from numbers verified in Twilio, and play a trial notice first.",
    ):
        info(line)
    if not number:
        info("To get a number: sign up at https://www.twilio.com/try-twilio, then Phone Numbers → Buy a number.")
    return number


def _post_status(url: str) -> tuple[int | None, str]:
    request = urllib.request.Request(url, method="POST", data=b"",
                                     headers={"content-type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(2000).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode(errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, ""


# --------------------------------------------------------------------------- main


def main() -> int:
    # Windows redirected consoles may use cp1252; status text must never stop a healthy service.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--no-tunnel", action="store_true",
        help="start no tunnel; PREAUTH_PUBLIC_BASE_URL already reaches this machine some other way",
    )
    args = parser.parse_args()

    config = load_config()
    processes = Processes()
    power_request_active = False
    # Fresh logs per run: the tunnel check reads its log, and yesterday's errors must not fail today's run.
    for name in ("backend", "tunnel"):
        (HOSTED_DIR / f"{name}.log").unlink(missing_ok=True)

    def shutdown(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    try:
        preflight(config, args)

        step("a", "Starting the backend")
        start_backend(config, processes)

        step("b", f"Starting the tunnel ({config['_TUNNEL']})")
        if args.no_tunnel:
            info("--no-tunnel: using PREAUTH_PUBLIC_BASE_URL as given")
        else:
            start_tunnel(config, processes)

        step("c", "Verifying the deployment through the public URL")
        verify(config, "initial")

        provider_name = "AssemblyAI"
        step("d", "Configuring the AssemblyAI voice agent")
        agent_ids = setup_provider(config)
        agent_changed = any(
            config.get(name) != agent_ids[channel]
            for channel, name in (
                ("browser", "PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID"),
                ("phone", "PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID"),
            )
        )
        config["PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID"] = agent_ids["browser"]
        config["PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID"] = agent_ids["phone"]
        step("e", "Applying AssemblyAI agent ids and webhook subscriptions")
        if agent_changed:
            info("restarting the backend so it picks up provider configuration")
            processes.stop("backend")
            start_backend(config, processes)
            step("e", "Re-verifying, now including the post-call webhook")
            verify(config, "with webhook")

        step("f", "Phone number (Twilio inbound media)")
        number = wire_phone_number(config)

        step("g", "Summary")
        talk = config["PREAUTH_PUBLIC_BASE_URL"] + "/voice"
        webhook_path = ASSEMBLYAI_WEBHOOK_PATH
        banner("LIVE — Sawt Assurance pre-authorisation line", "green")
        print(f"  Voice provider    {provider_name}")
        print(f"  Backend          {config['PREAUTH_PUBLIC_BASE_URL']}   (API docs: /docs)")
        print(f"  Test in browser  {talk}")
        print(f"  Phone            {number or 'not configured (see step f above)'}")
        print(f"  Twilio webhook   {config['PREAUTH_PUBLIC_BASE_URL']}{TWILIO_INBOUND_PATH}  (POST)")
        print(f"  Transcripts      {config['PREAUTH_PUBLIC_BASE_URL']}{webhook_path}")
        print(f"  Tunnel           {config['_TUNNEL']}")
        print("  Logs             .hosted/backend.log, .hosted/tunnel.log")
        print("\n  Live acceptance remains manual: call /voice and the Twilio number, then compare the")
        print("  transcript, identifier accuracy, first-audio latency and barge-in behavior (docs/VOICE_AGENT.md).")
        print("\n  Running. Press Ctrl+C to stop the backend and the tunnel.", flush=True)

        next_tunnel_probe = time.monotonic() + 15
        tunnel_failures = 0
        power_request_active = maintain_awake_on_ac(power_request_active)
        while True:
            gone = processes.exited()
            if gone:
                raise Stop(f"The {gone} process exited unexpectedly.\n{log_tail(gone)}")
            if config["_TUNNEL"] == "ngrok" and time.monotonic() >= next_tunnel_probe:
                tunnel_failures = check_ngrok_health(config, processes, tunnel_failures)
                power_request_active = maintain_awake_on_ac(power_request_active)
                next_tunnel_probe = time.monotonic() + 15
            time.sleep(1)
    except Stop as e:
        banner("STOPPED", "red")
        print(f"          {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopping.")
        return 0
    finally:
        if power_request_active:
            _set_execution_state(_ES_CONTINUOUS)
        # A second Ctrl+C (or a forwarded SIGTERM) must not interrupt the cleanup and leave a process behind.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        processes.stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
