"""One command from a Maya scene to a clean Unity prefab.

    artpipe build SCENE.ma --class prop_small [--texture T_X_D.png ...] [--out out/build]

Stages, each the same tool an agent could call on its own:
  1. maya    validate.maya.cli --fix --export, in its own mayapy process:
             validate, apply safe fixes, re-validate, export canonical FBX,
             re-validate the FBX. A failure here stops the build: nothing
             reaches Unity.
  2. unity   pipeline.unity import: gate the FBX (sidecar report, hash,
             settings, class), import it and the textures with the class
             rules, measure what the GPU gets, create the Prefab Variant.

Writes <out>/<stem>/build.report.json (contract:
conventions/build_report.schema.json), which links every stage report by
path. Same input, same bytes. Exit code is the worst stage: 0 pass /
1 an asset failed / 2 tool error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pipeline import unity

ROOT = unity.ROOT
SCHEMA_VERSION = "1.1"
TOOL = "artpipe.build 1.0"
MAYA_TIMEOUT_S = 15 * 60


def rel(path: Path) -> str:
    return unity.rel(Path(os.path.abspath(str(path))))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verdict(stages: Dict[str, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Overall status, exit code and the stage that stopped the build. Pure."""
    codes = [s["exit_code"] for s in stages.values() if s is not None]
    exit_code = 2 if 2 in codes else 1 if 1 in codes else 0
    stopped = next((name for name, s in stages.items() if s is not None and s["exit_code"] != 0), None)
    if stopped is None and any(s is None for s in stages.values()):
        stopped = "not_run"  # defensive: a stage was skipped without a failure before it
        exit_code = 2
    return {"exit_code": exit_code, "status": {0: "pass", 1: "fail", 2: "error"}[exit_code],
            "stopped_at": stopped}


def run_maya(scene: Path, asset_class: str, work: Path) -> Dict[str, Any]:
    """Stage 1, in a separate mayapy process (artpipe.cmd runs us under mayapy)."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]))
    cmd = [sys.executable, "-m", "validate.maya.cli", str(scene), "--class", asset_class,
           "--fix", "--fixed-dir", str(work / "fixed"), "--out", str(work / "maya"),
           "--export", str(work / "export")]
    try:
        rc = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=MAYA_TIMEOUT_S,
                            stdout=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        rc = 2
    report = work / "maya" / (scene.name + ".report.json")
    fbx = work / "export" / (scene.stem + ".fbx")
    fbx_report = work / "export" / (scene.stem + ".fbx.report.json")
    data = read_json(report)
    exported = read_json(fbx_report)
    if rc not in (0, 1, 2) or data is None:
        rc = 2  # no readable report: the tool broke, whatever Maya said
    return {
        "exit_code": rc,
        "status": data["status"] if data else "error",
        "report": rel(report) if data else None,
        "fixes_applied": data["summary"]["fixes_applied"] if data else 0,
        "fbx": rel(fbx) if exported and fbx.is_file() else None,
        "fbx_sha256": sha256(fbx) if exported and fbx.is_file() else None,
        "fbx_report": rel(fbx_report) if exported else None,
    }


def run_unity(fbx: Path, textures: Sequence[Path], asset_class: str, work: Path) -> Dict[str, Any]:
    """Stage 2: the batchmode import, through the same runner as `artpipe unity import`."""
    report = work / "unity.report.json"
    rc = unity.main(["import", "--class", asset_class, str(fbx)] + [str(t) for t in textures]
                    + ["--report", str(report)])
    data = read_json(report)
    model = None
    if data:
        model = next((a for a in data["assets"] if a["kind"] == "model"), None)
    return {
        "exit_code": rc,
        "status": data["status"] if data else "error",
        "report": rel(report) if data else None,
        "asset": model["path"] if model else None,
        "prefab": model["prefab"] if model else None,
        "textures": sorted(a["path"] for a in data["assets"] if a["kind"] == "texture") if data else [],
        "materials": list(model.get("materials") or []) if model else [],
    }


def build(scene: Path, asset_class: str, textures: Sequence[Path], out: Path) -> Dict[str, Any]:
    work = out / scene.stem
    work.mkdir(parents=True, exist_ok=True)
    stages: Dict[str, Optional[Dict[str, Any]]] = {"maya": run_maya(scene, asset_class, work), "unity": None}
    if stages["maya"]["exit_code"] == 0 and stages["maya"]["fbx"]:
        stages["unity"] = run_unity(ROOT / stages["maya"]["fbx"], textures, asset_class, work)
    v = verdict(stages)
    if stages["unity"] is None and v["stopped_at"] == "not_run":
        v["stopped_at"] = "maya"  # Maya passed but exported nothing
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "source": {"path": rel(scene), "sha256": sha256(scene)},
        "asset_class": asset_class,
        "textures": [{"path": rel(t), "sha256": sha256(t)} for t in sorted(textures, key=lambda p: p.name)],
        "status": v["status"],
        "stopped_at": v["stopped_at"],
        "stages": stages,
        "exit_code": v["exit_code"],
        "generation": None,  # filled by `artpipe generate` (pipeline/generate.py)
    }


def to_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe build", description=__doc__.split("\n\n")[0])
    p.add_argument("scene", type=Path, help="Maya scene (.ma/.mb) or FBX to take into Unity")
    p.add_argument("--class", dest="asset_class", required=True)
    p.add_argument("--texture", dest="textures", type=Path, action="append", default=[],
                   help="texture to import with it (repeatable)")
    p.add_argument("--out", type=Path, default=ROOT / "out" / "build")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    missing = [p for p in [args.scene] + list(args.textures) if not p.is_file()]
    if missing:
        print("artpipe build: not found: %s" % ", ".join(str(m) for m in missing), file=sys.stderr)
        return 2
    report = build(Path(os.path.abspath(str(args.scene))), args.asset_class,
                   [Path(os.path.abspath(str(t))) for t in args.textures], Path(os.path.abspath(str(args.out))))
    path = Path(os.path.abspath(str(args.out))) / args.scene.stem / "build.report.json"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(to_json(report))
    line = "%s %s" % (report["status"].upper(), rel(path))
    if report["stages"]["unity"] and report["stages"]["unity"]["prefab"]:
        line += " -> %s" % report["stages"]["unity"]["prefab"]
    elif report["stopped_at"]:
        line += " (stopped at %s)" % report["stopped_at"]
    print("artpipe build: " + line, file=sys.stdout if report["exit_code"] == 0 else sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
