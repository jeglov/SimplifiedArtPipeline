"""The ComfyUI server the pipeline owns: find it, start it headless, talk to it, stop it.

Standard library only (urllib, subprocess): runs under mayapy 3.9 like the
rest of artpipe. ComfyUI itself runs in its own Python (3.13, the Desktop
install's venv), in a separate process; we only speak HTTP to it.

Why the pipeline starts its own server instead of using the Desktop app's:
the app picks its own flags, output folder and port, and updates itself. Our
server always gets the same flags (no custom nodes, no PNG metadata), writes
under out/gen/comfy/, and is refused unless it is the pinned build. A server
on our port that we didn't start (the Desktop app) is a tool error, not
something to share: two ComfyUIs would fight over the GPU.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from gen.core import ToolError
from gen.core.manifest import git_head
from gen.core.rules import ComfyRules

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "gen"
WORK = OUT / "comfy"  # input/, output/, temp/, user/, server.log, server.json

# Every server the pipeline starts gets exactly these. --disable-metadata:
# SaveImage would otherwise embed the whole graph in the PNG, so the file hash
# would depend on the filename prefix (probed, docs/generation.md).
FLAGS = ("--disable-auto-launch", "--disable-all-custom-nodes", "--disable-metadata", "--log-stdout")

HTTP_TIMEOUT_S = 30
POLL_S = 0.25


def _local_appdata(env: Mapping[str, str]) -> Path:
    return Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def comfy_dir(env: Mapping[str, str] = os.environ) -> Path:
    """%ARTPIPE_COMFYUI% (the folder with main.py), else the Comfy Desktop install."""
    d = Path(env["ARTPIPE_COMFYUI"]) if env.get("ARTPIPE_COMFYUI") else \
        _local_appdata(env) / "Comfy-Desktop" / "ComfyUI-Installs" / "ComfyUI" / "ComfyUI"
    if not (d / "main.py").is_file():
        raise ToolError("ComfyUI not found at %s (no main.py). Install Comfy Desktop, or set "
                        "ARTPIPE_COMFYUI to the folder that holds main.py" % d)
    return d


def comfy_python(d: Path) -> Path:
    """ComfyUI's own interpreter: the Desktop install's uv venv (probed), not standalone-env."""
    for exe in (d / ".venv" / "Scripts" / "python.exe", d / ".venv" / "bin" / "python"):
        if exe.is_file():
            return exe
    raise ToolError("no ComfyUI Python at %s" % (d / ".venv"))


def models_dir(env: Mapping[str, str] = os.environ) -> Path:
    """%ARTPIPE_COMFY_MODELS%, else the folder Comfy Desktop downloads models into."""
    d = Path(env["ARTPIPE_COMFY_MODELS"]) if env.get("ARTPIPE_COMFY_MODELS") else \
        _local_appdata(env) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"
    if not d.is_dir():
        raise ToolError("model folder %s does not exist. Set ARTPIPE_COMFY_MODELS" % d)
    return d


def local_changes(d: Path) -> Optional[bool]:
    """True if tracked ComfyUI files were edited; None if git isn't available."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        out = subprocess.run([git, "-C", str(d), "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def check_install(d: Path, rules: ComfyRules) -> Tuple[str, Optional[bool]]:
    """Refuse anything but the pinned, unmodified ComfyUI, before starting it."""
    commit = git_head(d)
    if commit != rules.commit:
        raise ToolError("ComfyUI at %s is commit %s, the pipeline is pinned to %s (%s). Updating "
                        "changes outputs: change conventions/generation.yaml deliberately, or "
                        "check out the pinned commit" % (d, commit, rules.commit[:7], rules.version))
    dirty = local_changes(d)
    if dirty:
        raise ToolError("ComfyUI at %s has local edits to tracked files (git status). An edited "
                        "install isn't the pinned build" % d)
    return commit, dirty


def gpu_driver() -> Optional[str]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.stdout.strip().splitlines()
    return lines[0].strip() if out.returncode == 0 and lines else None


# --- HTTP -------------------------------------------------------------------------

class ServerLost(ToolError):
    """Nothing answers on the port: the server crashed or was stopped mid-run."""


class RunTimeout(ToolError):
    """The run did not finish within comfyui.run_timeout_s (it was interrupted)."""


class Client:
    def __init__(self, port: int) -> None:
        self.base = "http://127.0.0.1:%d" % port

    def _open(self, req: urllib.request.Request) -> Tuple[int, bytes]:
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            # Probed: ComfyUI can die mid-run (a Windows access violation in the
            # text encoder); the next poll gets "connection refused".
            raise ServerLost("ComfyUI at %s stopped answering (%s)" % (self.base, getattr(e, "reason", e)))

    def get(self, path: str, query: Optional[Mapping[str, str]] = None) -> Tuple[int, bytes]:
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        return self._open(urllib.request.Request(url))

    def get_json(self, path: str) -> Any:
        status, raw = self.get(path)
        if status != 200:
            raise ToolError("GET %s: HTTP %d" % (path, status))
        return json.loads(raw.decode("utf-8"))

    def post_json(self, path: str, body: Any) -> Tuple[int, Any]:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        status, raw = self._open(req)
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def stats(self) -> Optional[Dict[str, Any]]:
        """GET /system_stats, or None if nothing answers on the port."""
        try:
            return self.get_json("/system_stats")
        except (urllib.error.URLError, ConnectionError, OSError, ValueError, ToolError):
            return None


# --- the server process -----------------------------------------------------------

def _same_path(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(str(b)))


def is_ours(stats: Mapping[str, Any], work: Path = WORK) -> bool:
    """Started by us: its --output-directory is our folder (from /system_stats argv)."""
    argv = list((stats.get("system") or {}).get("argv") or [])
    for i, a in enumerate(argv[:-1]):
        if a == "--output-directory" and _same_path(argv[i + 1], work / "output"):
            return True
    return False


def model_paths_config(models: Path) -> str:
    """extra_model_paths config: every sub-folder of the model folder, by its own name.

    Written as JSON, which YAML (yaml.safe_load in ComfyUI) reads as-is, so a
    path with quotes or colons can't break it.
    """
    entry = {"base_path": str(models), "is_default": True}
    for sub in sorted(p.name for p in models.iterdir() if p.is_dir()):
        entry[sub] = sub + "/"
    return json.dumps({"artpipe": entry}, indent=2) + "\n"


def server_command(python: Path, port: int, config: Path, work: Path = WORK) -> List[str]:
    return [str(python), "main.py", "--listen", "127.0.0.1", "--port", str(port), *FLAGS,
            "--extra-model-paths-config", str(config),
            "--input-directory", str(work / "input"), "--output-directory", str(work / "output"),
            "--temp-directory", str(work / "temp"), "--user-directory", str(work / "user")]


def _log_tail(log: Path, lines: int = 15) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def crash_summary(log: Path, lines: int = 4) -> str:
    """The server log's last fatal error or traceback and the lines after it, else its tail."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for i in range(len(text) - 1, -1, -1):
        if "fatal exception" in text[i].lower() or text[i].startswith("Traceback"):
            return "\n".join(t for t in text[i:i + lines + 2] if t.strip())
    return "\n".join(text[-lines:])


def check_running(stats: Mapping[str, Any], rules: ComfyRules) -> None:
    version = (stats.get("system") or {}).get("comfyui_version")
    if version != rules.version:
        raise ToolError("the running ComfyUI reports version %s, pinned is %s" % (version, rules.version))


def start(rules: ComfyRules, env: Mapping[str, str] = os.environ, work: Path = WORK) -> bool:
    """Make sure our server is up. True if this call started it (so the caller stops it)."""
    client = Client(rules.port)
    stats = client.stats()
    if stats is not None:
        if not is_ours(stats, work):
            raise ToolError("another ComfyUI is already listening on port %d (probably the Comfy "
                            "Desktop app). Close it: the pipeline runs its own headless server"
                            % rules.port)
        check_running(stats, rules)
        return False

    d = comfy_dir(env)
    check_install(d, rules)
    python = comfy_python(d)
    for sub in ("input", "output", "temp", "user"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    config = work / "extra_model_paths.yaml"
    config.write_text(model_paths_config(models_dir(env)), encoding="utf-8")
    log = work / "server.log"
    flags = 0
    if sys.platform == "win32":
        # Own process group and no console: the server outlives `artpipe gen
        # server start`, and Ctrl+C in the caller's console doesn't reach it.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    with log.open("wb") as fh:
        proc = subprocess.Popen(server_command(python, rules.port, config, work), cwd=str(d),
                                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags)
    (work / "server.json").write_text(json.dumps({"pid": proc.pid, "port": rules.port}) + "\n",
                                      encoding="utf-8")
    deadline = time.time() + rules.startup_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            (work / "server.json").unlink()
            raise ToolError("ComfyUI exited during startup (code %d). Last lines of %s:\n%s"
                            % (proc.returncode, log, _log_tail(log)))
        stats = client.stats()
        if stats is not None:
            try:
                check_running(stats, rules)
            except ToolError:
                stop(work)
                raise
            return True
        time.sleep(0.5)
    stop(work)
    raise ToolError("ComfyUI did not answer within %d s. See %s" % (rules.startup_timeout_s, log))


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def stop(work: Path = WORK, port: Optional[int] = None) -> bool:
    """Stop the server we started (by the pid we recorded). False if there was none."""
    record = work / "server.json"
    try:
        info = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    _kill_tree(int(info["pid"]))
    client = Client(port or int(info["port"]))
    deadline = time.time() + 30
    while time.time() < deadline and client.stats() is not None:
        time.sleep(0.25)
    record.unlink()
    return True


# --- one graph --------------------------------------------------------------------

def queue(client: Client, graph: Mapping[str, Any], client_id: str) -> Tuple[Optional[str], Any]:
    """(prompt_id, None) when accepted, (None, (status, body)) when refused."""
    status, body = client.post_json("/prompt", {"prompt": graph, "client_id": client_id})
    if status == 200 and isinstance(body, dict) and body.get("prompt_id"):
        return body["prompt_id"], None
    return None, (status, body)


def wait(client: Client, prompt_id: str, timeout_s: int) -> Dict[str, Any]:
    """Poll /history until the run is finished. On timeout, interrupt it and fail."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        history = client.get_json("/history/" + prompt_id)
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(POLL_S)
    client.post_json("/interrupt", {})
    raise RunTimeout("the run did not finish within %d s and was interrupted" % timeout_s)


def download(client: Client, query: Mapping[str, str]) -> bytes:
    status, raw = client.get("/view", query)
    if status != 200:
        raise ToolError("GET /view %s: HTTP %d" % (dict(query), status))
    return raw
