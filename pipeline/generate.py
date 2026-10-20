"""One command from a text prompt to a Unity prefab.

    artpipe generate "SUBJECT" --class C --style S --name Name [--seed N] [--model-seed N] [--keep-server]

Stages, each the same tool an agent could call on its own:
  1. concept  gen concept --repair: concept image + mask, concept.* checks,
              retried by seed per conventions/generation.yaml `repair`
  2. model    gen model --repair on the accepted concept: TRELLIS.2 -> GLB
              at the class budgets, model.* checks, retried by seed
  3. ingest   gen ingest: GLB -> out/gen/assets/<Name>/SM_<Name>.ma + T_<Name>_D/N
  4. build    the ordinary `artpipe build`: Maya validate/fix/export, Unity
              gate/import/prefab, the shared material from T_<Name>_D/N (#49)

Generated assets get no shortcut: stage 4 is exactly what a hand-made asset
goes through. Writes out/build/SM_<Name>/build.report.json
(conventions/build_report.schema.json) with a `generation` block: subject,
style, every repair record and attempt, the accepted runs' manifests by
sha256, the models and their licences, GPU seconds. A stop before stage 4
still writes the report (stopped_at concept | model | ingest). Exit code is
the worst stage: 0 pass / 1 an asset failed / 2 tool error.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from gen import cli as gen
from gen import comfy
from gen.core import ToolError
from gen.core import rules as gen_rules
from pipeline import build as bld

ROOT = bld.ROOT
OUT = ROOT / "out" / "build"
DEFAULT_CONCEPT_SEED, DEFAULT_MODEL_SEED = 1, 56


def _file(path: Path) -> Dict[str, str]:
    return {"path": bld.rel(path), "sha256": bld.sha256(path)}


def stage_record(rec: Mapping[str, Any], repairs: Path) -> Dict[str, Any]:
    """A repair record, summarised for the build report and linked by sha256."""
    path = repairs / (rec["repair_id"] + ".json")
    run = rec["accepted_run"]
    manifest = ROOT / run / "manifest.json" if run else None
    return {
        "decision": rec["decision"],
        "reason": rec["reason"],
        "exit_code": rec["exit_code"],
        "attempts": rec["summary"]["attempts"],
        "seeds": [a["seed"] for a in rec["attempts"]],
        "failed": [a["failed"] for a in rec["attempts"]],
        "gpu_seconds": rec["summary"]["gpu_seconds"],
        "repair": _file(path),
        "run": run,
        "manifest": _file(manifest) if manifest is not None and manifest.is_file() else None,
    }


def models_used(stages: Mapping[str, Optional[Mapping[str, Any]]]) -> List[Dict[str, str]]:
    """Every pinned model behind the accepted runs, with its licence (from the manifests)."""
    seen: Dict[str, Dict[str, str]] = {}
    for s in stages.values():
        if not s or not s.get("manifest"):
            continue
        data = bld.read_json(ROOT / s["manifest"]["path"]) or {}
        for m in data.get("models") or []:
            seen[m["id"]] = {"id": m["id"], "sha256": m["sha256"], "licence": m["licence"]}
    return [seen[k] for k in sorted(seen)]


def environment(stages: Mapping[str, Optional[Mapping[str, Any]]]) -> Optional[Dict[str, Any]]:
    """The generator environment of the last accepted run (the nondeterministic part)."""
    for name in ("model", "concept"):
        s = stages.get(name)
        if s and s.get("manifest"):
            env = (bld.read_json(ROOT / s["manifest"]["path"]) or {}).get("environment") or {}
            return {k: env.get(k) for k in ("comfyui_version", "comfyui_commit", "python", "torch", "gpu",
                                            "vram_mb", "driver")}
    return None


def generation_block(subject: str, style: str, name: str,
                     stages: Mapping[str, Optional[Dict[str, Any]]],
                     ingest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "subject": subject,
        "style": style,
        "name": name,
        "stages": dict(stages, ingest=ingest),
        "models": models_used(stages),
        "environment": environment(stages),
        "gpu_seconds": round(sum(s["gpu_seconds"] for s in stages.values() if s), 3),
    }


def stopped_report(asset_class: str, stopped_at: str, exit_code: int, generation: Dict[str, Any]
                   ) -> Dict[str, Any]:
    """Build report for an asset that never reached the Maya stage."""
    return {
        "schema_version": bld.SCHEMA_VERSION,
        "tool": bld.TOOL,
        "source": None,
        "asset_class": asset_class,
        "textures": [],
        "status": {1: "fail", 2: "error"}[exit_code],
        "stopped_at": stopped_at,
        "stages": {"maya": None, "unity": None},
        "exit_code": exit_code,
        "generation": generation,
    }


def generate(subject: str, asset_class: str, style: str, name: str,
             concept_seed: int = DEFAULT_CONCEPT_SEED, model_seed: int = DEFAULT_MODEL_SEED,
             runs: Path = gen.RUNS, assets: Path = gen.ASSETS, out: Path = OUT,
             keep_server: bool = False, env: Mapping[str, str] = os.environ) -> Dict[str, Any]:
    gen.check_name(name)
    gen_rules.load_styles().prompt(subject, asset_class, style)  # unknown style or class: before the GPU
    repairs = runs.parent / "repairs"
    stages: Dict[str, Optional[Dict[str, Any]]] = {"concept": None, "model": None}
    port = gen_rules.load().comfyui.port
    was_up = comfy.Client(port).stats() is not None
    try:
        # The server stays up from concept to model: a cold start is ~15 s.
        code, _, rec = gen.concept_repaired(subject, asset_class, style, concept_seed, runs, True, env)
        stages["concept"] = stage_record(rec, repairs)
        if code != 0:
            return stopped_report(asset_class, "concept", code, generation_block(subject, style, name, stages, None))
        code, _, rec = gen.model_repaired(str(ROOT / rec["accepted_run"]), asset_class, model_seed, runs, True, env)
        stages["model"] = stage_record(rec, repairs)
        if code != 0:
            return stopped_report(asset_class, "model", code, generation_block(subject, style, name, stages, None))
    finally:
        if not was_up and not keep_server:
            comfy.stop()

    try:
        _, _, asset_dir = gen.ingest(str(ROOT / rec["accepted_run"]), asset_class, name, out_dir=assets)
    except (ToolError, gen_rules.ConventionError) as exc:
        ingest = {"exit_code": 2, "message": str(exc), "record": None}
        return stopped_report(asset_class, "ingest", 2, generation_block(subject, style, name, stages, ingest))
    ingest = {"exit_code": 0, "message": "", "record": _file(asset_dir / "ingest.json")}
    scene = asset_dir / ("SM_%s.ma" % name)
    textures = sorted(asset_dir.glob("T_%s_*.png" % name))
    report = bld.build(scene, asset_class, textures, out)
    report["generation"] = generation_block(subject, style, name, stages, ingest)
    return report


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe generate", description=__doc__.split("\n\n")[0])
    p.add_argument("subject", help='what the object is, e.g. "a golden coin with a star emblem"')
    p.add_argument("--class", dest="asset_class", required=True)
    p.add_argument("--style", required=True, help="style id from conventions/gen_styles.yaml")
    p.add_argument("--name", required=True, help="asset name: SM_<Name>, T_<Name>_D, M_<Name>")
    p.add_argument("--seed", type=int, default=DEFAULT_CONCEPT_SEED, help="concept base seed")
    p.add_argument("--model-seed", type=int, default=DEFAULT_MODEL_SEED, help="image-to-3D base seed")
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--keep-server", action="store_true", help="leave ComfyUI running afterwards")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    out = Path(os.path.abspath(str(args.out)))
    try:
        report = generate(args.subject, args.asset_class, args.style, args.name, args.seed, args.model_seed,
                          out=out, keep_server=args.keep_server)
    except (ToolError, gen_rules.ConventionError, KeyError) as exc:
        # Before any stage ran (unknown class or style, bad name): nothing to report.
        print("artpipe generate: %s" % str(exc).strip("'\""), file=sys.stderr)
        return 2
    path = out / ("SM_" + args.name) / "build.report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(bld.to_json(report))
    line = "%s %s" % (report["status"].upper(), bld.rel(path))
    unity = report["stages"]["unity"]
    if unity and unity["prefab"]:
        line += " -> %s" % unity["prefab"]
    elif report["stopped_at"]:
        line += " (stopped at %s)" % report["stopped_at"]
    print("artpipe generate: " + line, file=sys.stdout if report["exit_code"] == 0 else sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
