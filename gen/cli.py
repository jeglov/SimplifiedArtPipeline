"""Run a generation workflow on the pinned ComfyUI and record its provenance.

    artpipe gen run WORKFLOW [--param NAME VALUE ...] [--out DIR] [--keep-server]
    artpipe gen concept "SUBJECT" --class C --style S [--seed N] [--repair] [--out DIR] [--keep-server]
    artpipe gen model CONCEPT_RUN_OR_IMAGE --class C [--seed N] [--repair] [--out DIR] [--keep-server]
    artpipe gen ingest MODEL_RUN --class C --name Name [--size-cm N] [--out DIR]
    artpipe gen server start|stop|status

WORKFLOW: an id from gen/workflows/ (e.g. smoke_empty_image) or a path to a
workflow file. Writes <out>/<run_id>/ (default out/gen/runs/): the output
files plus manifest.json (contract: conventions/gen_manifest.schema.json).

A run id hashes everything that changes the output (filled graph, model
hashes, ComfyUI commit). If <out>/<run_id>/ already holds a successful
manifest whose files still match, the run is reused without starting
ComfyUI: same request, same files. Delete the folder to regenerate.

`run` starts the server if it isn't up and stops it afterwards, unless
--keep-server (or `server start` before a batch: startup is ~15 s).

--repair runs concept or model through the repair loop (gen/core/repair.py,
policy in generation.yaml `repair`): a failed check retries with seed
base + n x seed_step, or hands over; every attempt goes to
<out>/../repairs/<repair_id>.json (conventions/gen_repair.schema.json).

Exit codes: 0 files generated / 2 tool error (bad workflow, missing or
changed model, ComfyUI refused or failed the graph). 1 ("the asset is bad")
comes from the checks that judge the outputs, not from generation itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from gen import comfy
from gen.core import ToolError
from gen.core import history as hist
from gen.core import manifest as mf
from gen.core import rules as gen_rules
from gen.core import workflow as wfl
from gen.core import concept as concept_checks
from gen.core import ingest as ingest_core
from gen.core import model as model_checks
from gen.core import repair
from gen.core.rules import ModelPin
from validate.core import conventions as conv
from validate.core.glb import GlbError
from validate.core.glb import read as read_glb
from validate.core.image import ImageError, decode_png
from validate.core.paths import report_path
from validate.core.result import Report

RUNS = comfy.OUT / "runs"
ASSETS = comfy.OUT / "assets"
HASH_CACHE = comfy.OUT / "model_hashes.json"
EXIT_OK, EXIT_TOOL_ERROR = 0, 2
CONCEPT_WORKFLOW = "concept_zimage"
CONCEPT_TOOL = "artpipe.gen.concept"
MODEL_WORKFLOW = "model_trellis2"
MODEL_TOOL = "artpipe.gen.model"
CONCEPT_STAGE, MODEL_STAGE = "concept", "model"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_sha256(path: Path, cache_file: Optional[Path] = None) -> str:
    """sha256 of a (multi-GB) model file, remembered by (size, mtime).

    Hashing a 6 GB file takes ~20 s; a run must still prove the file is the
    pinned one. A replaced file gets a new mtime, so the cache can't hide it.
    """
    cache_file = cache_file or HASH_CACHE
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


def verify_models(pins: Sequence[ModelPin], models: Optional[Path]) -> List[Tuple[ModelPin, str]]:
    out = []
    for pin in pins:
        if models is None:
            raise ToolError("model folder unknown (set ARTPIPE_COMFY_MODELS)")
        path = models / pin.folder / pin.file
        if not path.is_file():
            raise ToolError("model %s missing: %s (download it from %s)" % (pin.id, path, pin.source))
        digest = cached_sha256(path)
        if digest != pin.sha256:
            raise ToolError("model %s at %s has sha256 %s, pinned %s: a different file under the "
                            "pinned name" % (pin.id, path, digest[:12], pin.sha256[:12]))
        out.append((pin, digest))
    return out


def machine_swaps(env: Mapping[str, str] = os.environ) -> List[Tuple[str, str]]:
    """This machine's folders -> tokens for gen.core.manifest.scrub, most specific first (backlog #46)."""
    swaps = []
    for find, token in ((comfy.comfy_dir, "<comfyui>"), (comfy.models_dir, "<models>")):
        try:
            swaps.append((str(find(env)), token))
        except ToolError:
            pass  # not installed: nothing of it can appear in a message
    return swaps + [(str(comfy.ROOT), "<repo>"), (str(Path.home()), "<home>")]


def reusable(run_dir: Path) -> Optional[Dict[str, Any]]:
    """The previous manifest if that run succeeded and its files are untouched."""
    try:
        data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("status") != "ok" or not data.get("outputs"):
        return None
    for out in data["outputs"]:
        f = run_dir / Path(out["path"]).name
        if not f.is_file() or sha256_file(f) != out["sha256"]:
            return None
    return data


def stage_inputs(wf: wfl.Workflow, values: Mapping[str, Any]
                 ) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[Tuple[Path, str]]]:
    """File params -> (values for the manifest, values for the graph, inputs record, copies to make).

    The graph gets "<sha256><ext>": a name ComfyUI finds in our input folder
    and that changes exactly when the content does.
    """
    shown, graph_values, inputs, copies = dict(values), dict(values), [], []
    for name, p in sorted(wf.params.items()):
        if p.type != "file":
            continue
        src = Path(str(values[name]))
        if not str(values[name]) or not src.is_file():
            raise ToolError("--param %s: file not found: %r" % (name, str(values[name])))
        digest = sha256_file(src)
        staged = digest + src.suffix.lower()
        shown[name] = report_path(src)
        graph_values[name] = staged
        inputs.append({"param": name, "path": report_path(src), "sha256": digest})
        copies.append((src, staged))
    return shown, graph_values, inputs, copies


def run(workflow_arg: str, pairs: Sequence[str], out_dir: Path = RUNS, keep_server: bool = False,
        env: Mapping[str, str] = os.environ) -> Tuple[int, str]:
    code, message, _ = generate(workflow_arg, pairs, out_dir, keep_server, env)
    return code, message


def generate(workflow_arg: str, pairs: Sequence[str], out_dir: Path = RUNS, keep_server: bool = False,
             env: Mapping[str, str] = os.environ) -> Tuple[int, str, Path]:
    """Run one workflow. (exit code, message, run folder with manifest.json)."""
    rules = gen_rules.load()
    wf = wfl.load(Path(workflow_arg), rules)
    values, graph_values, inputs, copies = stage_inputs(
        wf, wfl.resolve_params(wf, wfl.parse_overrides(list(pairs), wf)))
    graph = wfl.fill(wf, graph_values)
    pins = wfl.pins_for(wf, rules)
    models = verify_models(pins, comfy.models_dir(env) if pins else None)
    rid = wfl.run_id(wf, graph, {p.id: sha for p, sha in models}, rules.comfyui.commit)
    run_dir = out_dir / rid
    if reusable(run_dir) is not None:
        return EXIT_OK, "OK (reused) %s" % report_path(run_dir / "manifest.json"), run_dir

    started = comfy.start(rules.comfyui, env)
    try:
        client = comfy.Client(rules.comfyui.port)
        stats = client.stats() or {}
        env_record = mf.environment(stats, mf.git_head(comfy.comfy_dir(env)),
                                    comfy.local_changes(comfy.comfy_dir(env)), comfy.gpu_driver())
        for src, staged in copies:
            (comfy.WORK / "input").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(src), str(comfy.WORK / "input" / staged))
        if run_dir.exists():
            shutil.rmtree(str(run_dir))  # a failed or tampered earlier attempt
        run_dir.mkdir(parents=True)
        prompt_graph = wfl.with_prefixes(wf, graph, rid)
        prompt_id, execution, outputs, error = None, None, [], None
        try:
            prompt_id, refused = comfy.queue(client, prompt_graph, "artpipe-" + uuid.uuid4().hex)
            if refused is not None:
                error = _error("prompt_refused", hist.prompt_error(*refused))
            else:
                execution = hist.parse_history(comfy.wait(client, prompt_id, rules.comfyui.run_timeout_s),
                                               prompt_graph)
                error = execution.error
                if execution.ok:
                    outputs = _collect(client, execution, wf, run_dir)
                    if not outputs:
                        error = _error("no_outputs", "the run succeeded but produced no declared output file")
        except comfy.ServerLost as exc:
            crash = comfy.crash_summary(comfy.WORK / "server.log")
            error = _error("server_lost", "%s: it crashed or was stopped during the run%s"
                           % (exc, (". Server log:\n" + crash) if crash else ""))
        except comfy.RunTimeout as exc:
            error = _error("timeout", str(exc))
        if error is not None:
            # Exception messages and tracebacks quote absolute paths (#46).
            error = mf.scrub(error, machine_swaps(env))
        manifest = mf.build(wf=wf, workflow_path=report_path(wf.path), params=values, rid=rid,
                            prompt_id=prompt_id, models=models, env=env_record,
                            execution=execution, outputs=outputs, error=error, inputs=inputs)
        path = run_dir / "manifest.json"
        path.write_text(mf.to_json(manifest), encoding="utf-8")
    finally:
        if started and not keep_server:
            comfy.stop()
    if error is not None:
        where = " in node %s (%s)" % (error["node"], error["node_type"]) if error.get("node") else ""
        return EXIT_TOOL_ERROR, "%s%s: %s. See %s" % (error["type"], where, error["message"],
                                                      report_path(path)), run_dir
    return EXIT_OK, "OK %s" % report_path(path), run_dir


def _error(kind: str, message: str) -> Dict[str, Any]:
    return {"type": kind, "message": message, "node": None, "node_type": None, "exception_type": None}


def _collect(client: comfy.Client, ex: hist.Execution, wf: wfl.Workflow, run_dir: Path) -> List[Dict[str, Any]]:
    outputs = []
    for name, local, f in mf.output_names(ex.files, wf):
        data = comfy.download(client, f.query())
        (run_dir / local).write_bytes(data)
        outputs.append({"name": name, "path": report_path(run_dir / local),
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return outputs


def _verdict(report: Report, path: Path, generate_message: str) -> str:
    """"PASS out/.../x.report.json", with "(reused)" when generation reused an earlier run."""
    reused = " (reused)" if generate_message.startswith("OK (reused)") else ""
    return "%s%s %s" % (report.status.value.upper(), reused, report_path(path))


def concept(subject: str, asset_class: str, style: str, seed: int = 0, out_dir: Path = RUNS,
            keep_server: bool = False, env: Mapping[str, str] = os.environ) -> Tuple[int, str, Path]:
    """Concept image for one asset, then the image-to-3D suitability checks.

    Writes <run>/concept.report.json (report.schema.json, tool artpipe.gen.concept).
    Exit 0 the image is usable / 1 it isn't (the report says why) / 2 tool error.
    """
    c = conv.load()
    c.asset_class(asset_class)  # unknown class: KeyError listing the classes
    styles = gen_rules.load_styles(class_names=c.class_names)
    prompt = styles.prompt(subject, asset_class, style)
    code, message, run_dir = generate(CONCEPT_WORKFLOW, ["prompt=" + prompt, "seed=%d" % seed],
                                      out_dir, keep_server, env)
    if code != EXIT_OK:
        return code, message, run_dir
    image = run_dir / "image.png"
    shown = report_path(image)
    try:
        results = concept_checks.check(decode_png(image), decode_png(run_dir / "mask.png"),
                                       gen_rules.load().concept, shown)
        report = Report(shown, sha256_file(image), asset_class, results=results, tool=CONCEPT_TOOL)
    except (ImageError, ValueError, OSError) as exc:
        report = Report(shown, sha256_file(image), asset_class, tool_error=str(exc), tool=CONCEPT_TOOL)
    out = run_dir / "concept.report.json"
    out.write_text(report.to_json(), encoding="utf-8")
    return report.exit_code, _verdict(report, out, message), run_dir


def model(source: str, asset_class: str, seed: int = 56, out_dir: Path = RUNS,
          keep_server: bool = False, env: Mapping[str, str] = os.environ) -> Tuple[int, str, Path]:
    """Image to textured low-poly GLB at the class budgets, then the model.* checks.

    `source`: a concept run folder (its image.png; refused unless its
    concept.report.json passed) or any image file. Writes <run>/model.report.json.
    Exit 0 usable / 1 not (the report says why) / 2 tool error.
    """
    c = conv.load()
    cls = c.asset_class(asset_class)
    src = Path(source)
    if src.is_dir():
        verdict = src / "concept.report.json"
        try:
            status = json.loads(verdict.read_text(encoding="utf-8"))["status"]
        except (OSError, ValueError, KeyError):
            raise ToolError("%s has no readable concept.report.json: run `artpipe gen concept` first" % src)
        if status != "pass":
            raise ToolError("the concept image in %s failed its checks (%s): image-to-3D would "
                            "model the problem. Generate a new concept first" % (src, verdict))
        src = src / "image.png"
    rules = gen_rules.load().model
    size = cls.texture_max_size
    pairs = ["image=%s" % src, "seed=%d" % seed, "faces=%d" % rules.target_faces(cls.tris_max),
             "texture_size=%d" % size, "normal_size=%d" % size, "atlas_size=%d" % size,
             "remesh_resolution=%d" % rules.remesh_resolution]
    code, message, run_dir = generate(MODEL_WORKFLOW, pairs, out_dir, keep_server, env)
    if code != EXIT_OK:
        return code, message, run_dir
    glb_path = run_dir / "model.glb"
    shown = report_path(glb_path)
    try:
        report = Report(shown, sha256_file(glb_path), asset_class,
                        results=model_checks.check(read_glb(glb_path), cls, shown, rules.uv_overlap_max_share),
                        tool=MODEL_TOOL)
    except (GlbError, OSError, KeyError, IndexError, struct.error) as exc:
        report = Report(shown, sha256_file(glb_path) if glb_path.is_file() else "", asset_class,
                        tool_error="unreadable GLB: %s" % exc, tool=MODEL_TOOL)
    out = run_dir / "model.report.json"
    out.write_text(report.to_json(), encoding="utf-8")
    return report.exit_code, _verdict(report, out, message), run_dir


def check_name(name: str, c: Optional[conv.Conventions] = None) -> str:
    """--name -> "SM_<Name>", or ToolError: checked before any GPU time is spent."""
    c = c or conv.load()
    stem = "SM_" + name
    check = c.naming.check("mesh_file", stem + ".fbx")
    if not check.ok or stem.endswith("_LOD0"):
        raise ToolError("--name %r: %s" % (name, check.message))
    return stem


def ingest(model_run: str, asset_class: str, name: str, size_cm: Optional[float] = None,
           out_dir: Path = ASSETS) -> Tuple[int, str, Path]:
    """A passing model run -> <out>/<Name>/SM_<Name>.ma + T_<Name>_D/_N.png (needs mayapy).

    Then the normal path: `artpipe build SM_<Name>.ma --class C --texture ...`.
    """
    c = conv.load()
    cls = c.asset_class(asset_class)
    run_dir = Path(model_run)
    try:
        status = json.loads((run_dir / "model.report.json").read_text(encoding="utf-8"))["status"]
    except (OSError, ValueError, KeyError):
        raise ToolError("%s has no readable model.report.json: run `artpipe gen model` first" % run_dir)
    if status != "pass":
        raise ToolError("the model in %s failed its checks: regenerate it before ingesting" % run_dir)
    stem = check_name(name, c)
    rules = gen_rules.load().ingest
    if size_cm is None:
        if asset_class not in rules.size_cm:
            raise ToolError("no ingest.size_cm for class %s in generation.yaml: pass --size-cm" % asset_class)
        size_cm = rules.size_cm[asset_class]
    if not cls.size_cm_min <= size_cm <= cls.size_cm_max:
        raise ToolError("size %s cm is outside %s's %s-%s cm" % (size_cm, asset_class, cls.size_cm_min,
                                                                  cls.size_cm_max))
    glb = read_glb(run_dir / "model.glb")
    arrays = ingest_core.arrays(glb, size_cm, cls.pivot.mode)

    from gen.ingest import build_scene
    from validate.maya import session
    session.start()
    try:
        scene = build_scene(arrays, name, out_dir / name,
                            {"D": run_dir / "base_color.png", "N": run_dir / "normal.png"})
    finally:
        session.stop()
    record = {"scene": report_path(scene), "asset_class": asset_class, "size_cm": size_cm,
              "pivot_mode": cls.pivot.mode, "scale_from_glb": round(arrays.scale, 6),
              "dropped_degenerate_triangles": arrays.dropped,
              "model_run": {"path": report_path(run_dir / "manifest.json"),
                            "sha256": sha256_file(run_dir / "manifest.json")},
              "textures": sorted(report_path(p) for p in (out_dir / name).glob("T_*.png"))}
    (out_dir / name / "ingest.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                                                encoding="utf-8")
    return EXIT_OK, "OK %s" % report_path(scene), out_dir / name


def _outcome(call: Callable[[], Tuple[int, str, Path]], report_name: str) -> repair.Outcome:
    """One stage run as the repair loop sees it. A ToolError is an outcome (exit 2), not a crash."""
    try:
        code, message, run_dir = call()
    except (ToolError, gen_rules.ConventionError, GlbError) as exc:
        return repair.Outcome(EXIT_TOOL_ERROR, message=mf.scrub(str(exc).strip("'\""), machine_swaps()))
    report = _read_json(run_dir / report_name) if code != EXIT_TOOL_ERROR else None
    execution = (_read_json(run_dir / "manifest.json") or {}).get("execution") or {}
    return repair.Outcome(code, report, report_path(run_dir / report_name) if report else None,
                          report_path(run_dir), execution.get("seconds"), "(reused)" in message,
                          mf.scrub(message, machine_swaps()))


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def with_repair(stage: str, request: Mapping[str, Any], base_seed: int,
                fn: Callable[[int], repair.Outcome], out_dir: Path = RUNS, keep_server: bool = False
                ) -> Tuple[int, str, Dict[str, Any]]:
    """Run a stage through the repair loop; write <out_dir>/../repairs/<repair_id>.json.

    The server stays up between attempts (a cold start is ~15 s) and is
    stopped at the end unless it was already running or --keep-server.
    """
    rules = gen_rules.load()
    pol = repair.policy(rules.repair)
    was_up = comfy.Client(rules.comfyui.port).stats() is not None
    try:
        last, attempts = repair.run(stage, base_seed, pol, fn)
    finally:
        if not was_up and not keep_server:
            comfy.stop()
    rec = repair.record(stage, request, pol, last, attempts)
    path = out_dir.parent / "repairs" / (rec["repair_id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, indent=2, sort_keys=False, ensure_ascii=False) + "\n")
    what = {repair.ACCEPT: "PASS", repair.STOP: "HANDED OVER" if rec["exit_code"] == 1 else "ERROR"}
    message = "%s after %d attempt(s): %s. See %s" % (what[last.decision], len(attempts), last.reason,
                                                     report_path(path))
    if last.decision == repair.ACCEPT:
        message += " -> %s" % last.run
    return rec["exit_code"], message, rec


def concept_repaired(subject: str, asset_class: str, style: str, seed: int = 0, out_dir: Path = RUNS,
                     keep_server: bool = False, env: Mapping[str, str] = os.environ
                     ) -> Tuple[int, str, Dict[str, Any]]:
    """`gen concept --repair`: retry by seed per conventions/generation.yaml `repair`."""
    request = {"subject": subject, "asset_class": asset_class, "style": style, "base_seed": seed}
    return with_repair(CONCEPT_STAGE, request, seed, lambda s: _outcome(
        lambda: concept(subject, asset_class, style, s, out_dir, True, env), "concept.report.json"),
        out_dir, keep_server)


def model_repaired(source: str, asset_class: str, seed: int = 56, out_dir: Path = RUNS,
                   keep_server: bool = False, env: Mapping[str, str] = os.environ
                   ) -> Tuple[int, str, Dict[str, Any]]:
    """`gen model --repair`. The request names the image by content, not by path."""
    src = Path(source)
    image = src / "image.png" if src.is_dir() else src
    request = {"image_sha256": sha256_file(image) if image.is_file() else None,
               "asset_class": asset_class, "base_seed": seed}
    return with_repair(MODEL_STAGE, request, seed, lambda s: _outcome(
        lambda: model(source, asset_class, s, out_dir, True, env), "model.report.json"),
        out_dir, keep_server)


def server(action: str, env: Mapping[str, str] = os.environ) -> Tuple[int, str]:
    rules = gen_rules.load().comfyui
    if action == "start":
        started = comfy.start(rules, env)
        return EXIT_OK, ("started on port %d" if started else "already running on port %d") % rules.port
    if action == "stop":
        return EXIT_OK, "stopped" if comfy.stop() else "not running (nothing started by artpipe)"
    stats = comfy.Client(rules.port).stats()
    if stats is None:
        return EXIT_OK, "not running"
    owner = "artpipe" if comfy.is_ours(stats) else "NOT artpipe (Desktop app?)"
    return EXIT_OK, "running on port %d, ComfyUI %s, started by %s" % (
        rules.port, (stats.get("system") or {}).get("comfyui_version"), owner)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe gen", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run a workflow and write its outputs + manifest")
    r.add_argument("workflow", help="id in gen/workflows/ or a workflow file")
    # NAME VALUE, not NAME=VALUE: artpipe.cmd is a batch file, and batch
    # splits arguments on "=" (probed: "color=255" arrived as two arguments).
    r.add_argument("--param", action="append", default=[], nargs=2, metavar=("NAME", "VALUE"))
    r.add_argument("--out", default=str(RUNS), help="runs folder (default out/gen/runs)")
    r.add_argument("--keep-server", action="store_true", help="leave ComfyUI running afterwards")
    k = sub.add_parser("concept", help="concept image for one asset + image-to-3D suitability checks")
    k.add_argument("subject", help='what the object is, e.g. "a golden coin with a star"')
    k.add_argument("--class", dest="asset_class", required=True)
    k.add_argument("--style", required=True, help="style id from conventions/gen_styles.yaml")
    k.add_argument("--seed", type=int, default=0)
    k.add_argument("--repair", action="store_true", help="retry failed checks by seed (generation.yaml repair)")
    k.add_argument("--out", default=str(RUNS))
    k.add_argument("--keep-server", action="store_true")
    m = sub.add_parser("model", help="image to textured low-poly GLB at the class budgets + model checks")
    m.add_argument("source", help="a concept run folder (out/gen/runs/concept_zimage-...) or an image")
    m.add_argument("--class", dest="asset_class", required=True)
    m.add_argument("--seed", type=int, default=56)
    m.add_argument("--repair", action="store_true", help="retry failed checks by seed (generation.yaml repair)")
    m.add_argument("--out", default=str(RUNS))
    m.add_argument("--keep-server", action="store_true")
    i = sub.add_parser("ingest", help="a passing model run -> Maya scene + textures (mayapy)")
    i.add_argument("model_run", help="out/gen/runs/model_trellis2-...")
    i.add_argument("--class", dest="asset_class", required=True)
    i.add_argument("--name", required=True, help="asset name, e.g. Coin -> SM_Coin.ma, T_Coin_D.png")
    i.add_argument("--size-cm", type=float, default=None, help="largest dimension (default: per class)")
    i.add_argument("--out", default=str(ASSETS))
    s = sub.add_parser("server", help="start/stop/status of the pipeline's ComfyUI")
    s.add_argument("action", choices=("start", "stop", "status"))
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "run":
            pairs = ["%s=%s" % (name, value) for name, value in args.param]
            code, message = run(args.workflow, pairs, Path(os.path.abspath(args.out)), args.keep_server)
        elif args.command == "model":
            fn = model_repaired if args.repair else model
            code, message, _ = fn(args.source, args.asset_class, args.seed,
                                  Path(os.path.abspath(args.out)), args.keep_server)
        elif args.command == "ingest":
            code, message, _ = ingest(args.model_run, args.asset_class, args.name, args.size_cm,
                                      Path(os.path.abspath(args.out)))
        elif args.command == "concept":
            fn = concept_repaired if args.repair else concept
            code, message, _ = fn(args.subject, args.asset_class, args.style, args.seed,
                                  Path(os.path.abspath(args.out)), args.keep_server)
        else:
            code, message = server(args.action)
    except (ToolError, gen_rules.ConventionError, KeyError, GlbError) as exc:
        code, message = EXIT_TOOL_ERROR, str(exc).strip("'\"")
    print("artpipe gen: %s" % message, file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
