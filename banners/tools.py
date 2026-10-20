"""The renderer's pinned tools: install them, prove they are the pinned bytes, find them.

    ffmpeg + ffprobe   %ARTPIPE_TOOLS%\\ffmpeg-<version>\\        (default %LOCALAPPDATA%\\artpipe)
    Chromium shell     %ARTPIPE_TOOLS%\\chromium-<version>\\
    Playwright         banners/render/node_modules (npm ci --ignore-scripts, package-lock.json)

`setup()` downloads each zip from conventions/banners.yaml, refuses it unless
its sha256 matches, unpacks only the pinned folder, then checks every exe's
sha256. `require()` repeats the exe checks before every use (cached by size +
mtime, so it costs nothing after the first run): a swapped binary changes
the output bytes without changing its name.

Downloads go through urllib, not npm or Playwright: Playwright's own
browser downloader timed out on this machine (docs/banners.md).
Standard library only, Python 3.9 (mayapy).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from banners.core import ToolError
from banners.core.rules import BannerRules, ToolPin

ROOT = Path(__file__).resolve().parents[1]
RENDER_DIR = Path(__file__).resolve().parent / "render"
DOWNLOAD_TIMEOUT_S = 120


def tools_dir(env: Mapping[str, str] = os.environ) -> Path:
    if env.get("ARTPIPE_TOOLS"):
        return Path(env["ARTPIPE_TOOLS"])
    return Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "artpipe"


def install_dir(pin: ToolPin, env: Mapping[str, str] = os.environ) -> Path:
    return tools_dir(env) / ("%s-%s" % (pin.id, pin.version))


def exe_path(pin: ToolPin, name: str, env: Mapping[str, str] = os.environ) -> Path:
    return install_dir(pin, env) / (name + ".exe")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_sha256(path: Path, cache_file: Path) -> str:
    """sha256 remembered by (size, mtime_ns): a replaced file gets a new mtime, so the cache can't hide it."""
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    st = path.stat()
    key = os.path.normcase(str(path.resolve()))
    hit = cache.get(key)
    if hit and hit.get("size") == st.st_size and hit.get("mtime_ns") == st.st_mtime_ns:
        return hit["sha256"]
    digest = sha256_file(path)
    cache[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return digest


def verify(pin: ToolPin, env: Mapping[str, str] = os.environ) -> Dict[str, str]:
    """{exe name: absolute path} after every exe matched its pin. ToolError otherwise."""
    cache = tools_dir(env) / "exe_hashes.json"
    found = {}
    for name, pinned in sorted(pin.exe_sha256.items()):
        path = exe_path(pin, name, env)
        if not path.is_file():
            raise ToolError("%s %s is not installed (%s missing): run `artpipe banners setup`"
                            % (pin.id, pin.version, path))
        digest = cached_sha256(path, cache)
        if digest != pinned:
            raise ToolError("%s has sha256 %s, pinned %s: not the pinned %s build. Delete %s and run "
                            "`artpipe banners setup`" % (path, digest[:12], pinned[:12], pin.id,
                                                         install_dir(pin, env)))
        found[name] = str(path)
    return found


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
    except OSError as exc:
        raise ToolError("download failed: %s (%s)" % (url, exc))
    os.replace(str(tmp), str(dest))


def unpack(archive: Path, folder: str, dest: Path) -> int:
    """Extract only `folder/` of the zip into dest (flattened one level). Returns the file count.

    Refuses any member that would land outside dest (a zip-slip archive).
    """
    prefix = folder.strip("/") + "/"
    staging = dest.with_name(dest.name + ".unpacking")
    if staging.exists():
        shutil.rmtree(str(staging))
    count = 0
    with zipfile.ZipFile(str(archive)) as zf:
        for info in zf.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            rel = info.filename[len(prefix):]
            target = (staging / rel).resolve()
            if staging.resolve() not in target.parents:
                raise ToolError("%s: member %r escapes the install folder" % (archive.name, info.filename))
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            count += 1
    if not count:
        raise ToolError("%s has no folder %r" % (archive.name, folder))
    if dest.exists():
        shutil.rmtree(str(dest))
    os.replace(str(staging), str(dest))
    return count


def install(pin: ToolPin, env: Mapping[str, str] = os.environ) -> str:
    """Install one tool unless it is already there and verified. Returns what happened."""
    try:
        verify(pin, env)
        return "%s %s: already installed" % (pin.id, pin.version)
    except ToolError:
        pass
    archive = tools_dir(env) / "downloads" / ("%s-%s.zip" % (pin.id, pin.version))
    if not archive.is_file() or sha256_file(archive) != pin.archive_sha256:
        _download(pin.source, archive)
    digest = sha256_file(archive)
    if digest != pin.archive_sha256:
        raise ToolError("%s download has sha256 %s, pinned %s: refusing it (%s)"
                        % (pin.id, digest[:12], pin.archive_sha256[:12], pin.source))
    files = unpack(archive, pin.archive_dir, install_dir(pin, env))
    verify(pin, env)
    return "%s %s: installed %d files" % (pin.id, pin.version, files)


def node() -> str:
    found = shutil.which("node")
    if not found:
        raise ToolError("node not found on PATH (Node.js >= 20 runs the banner renderer)")
    return found


def playwright_version(render_dir: Path = RENDER_DIR) -> Optional[str]:
    try:
        data = json.loads((render_dir / "node_modules" / "playwright" / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("version")


def install_node_modules(rules: BannerRules, render_dir: Path = RENDER_DIR) -> str:
    if playwright_version(render_dir) == rules.playwright.version:
        return "playwright %s: already installed" % rules.playwright.version
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise ToolError("npm not found on PATH")
    # --ignore-scripts: Playwright's postinstall would try to download browsers.
    proc = subprocess.run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=str(render_dir),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    got = playwright_version(render_dir)
    if proc.returncode != 0 or got != rules.playwright.version:
        raise ToolError("npm ci failed in %s (playwright %s, pinned %s):\n%s"
                        % (render_dir, got, rules.playwright.version, proc.stdout[-2000:]))
    return "playwright %s: installed" % got


def setup(rules: BannerRules, env: Mapping[str, str] = os.environ) -> List[str]:
    return [install(rules.ffmpeg, env), install(rules.chromium, env), install_node_modules(rules)]


def status(rules: BannerRules, env: Mapping[str, str] = os.environ) -> Dict[str, Any]:
    """Machine-readable state of every pinned tool (never raises)."""
    out = {}
    for pin in (rules.ffmpeg, rules.chromium):
        try:
            verify(pin, env)
            out[pin.id] = {"version": pin.version, "ok": True, "path": str(install_dir(pin, env))}
        except ToolError as exc:
            out[pin.id] = {"version": pin.version, "ok": False, "error": str(exc)}
    got = playwright_version()
    out["playwright"] = {"version": rules.playwright.version, "installed": got,
                         "ok": got == rules.playwright.version}
    return out


def require(rules: BannerRules, env: Mapping[str, str] = os.environ) -> Dict[str, str]:
    """Paths of every exe the renderer runs, all verified. ToolError names the missing piece."""
    paths = dict(verify(rules.ffmpeg, env))
    paths.update(verify(rules.chromium, env))
    got = playwright_version()
    if got != rules.playwright.version:
        raise ToolError("playwright %s installed, pinned %s: run `artpipe banners setup`"
                        % (got, rules.playwright.version))
    paths["node"] = node()
    return paths
