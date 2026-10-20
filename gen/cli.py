"""Run a generation workflow on the pinned ComfyUI and record its provenance.

    artpipe gen run WORKFLOW [--param NAME VALUE ...] [--out DIR] [--keep-server]
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
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gen import comfy
from gen.core import ToolError
from gen.core import history as hist
from gen.core import manifest as mf
from gen.core import rules as gen_rules
from gen.core import workflow as wfl
from gen.core.rules import ModelPin
from validate.core.paths import report_path

RUNS = comfy.OUT / "runs"
HASH_CACHE = comfy.OUT / "model_hashes.json"
EXIT_OK, EXIT_TOOL_ERROR = 0, 2


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


def run(workflow_arg: str, pairs: Sequence[str], out_dir: Path = RUNS, keep_server: bool = False,
        env: Mapping[str, str] = os.environ) -> Tuple[int, str]:
    rules = gen_rules.load()
    wf = wfl.load(Path(workflow_arg), rules)
    values = wfl.resolve_params(wf, wfl.parse_overrides(list(pairs), wf))
    graph = wfl.fill(wf, values)
    pins = wfl.pins_for(wf, rules)
    models = verify_models(pins, comfy.models_dir(env) if pins else None)
    rid = wfl.run_id(wf, graph, {p.id: sha for p, sha in models}, rules.comfyui.commit)
    run_dir = out_dir / rid
    if reusable(run_dir) is not None:
        return EXIT_OK, "OK (reused) %s" % report_path(run_dir / "manifest.json")

    started = comfy.start(rules.comfyui, env)
    try:
        client = comfy.Client(rules.comfyui.port)
        stats = client.stats() or {}
        env_record = mf.environment(stats, mf.git_head(comfy.comfy_dir(env)),
                                    comfy.local_changes(comfy.comfy_dir(env)), comfy.gpu_driver())
        if run_dir.exists():
            shutil.rmtree(str(run_dir))  # a failed or tampered earlier attempt
        run_dir.mkdir(parents=True)
        prompt_graph = wfl.with_prefixes(wf, graph, rid)
        prompt_id, refused = comfy.queue(client, prompt_graph, "artpipe-" + uuid.uuid4().hex)
        execution, outputs, error = None, [], None
        if refused is not None:
            error = {"type": "prompt_refused", "message": hist.prompt_error(*refused),
                     "node": None, "node_type": None, "exception_type": None}
        else:
            execution = hist.parse_history(comfy.wait(client, prompt_id, rules.comfyui.run_timeout_s),
                                           prompt_graph)
            error = execution.error
            if execution.ok:
                outputs = _collect(client, execution, wf, run_dir)
                if not outputs:
                    error = {"type": "no_outputs", "node": None, "node_type": None,
                             "exception_type": None,
                             "message": "the run succeeded but produced no declared output file"}
        manifest = mf.build(wf=wf, workflow_path=report_path(wf.path), params=values, rid=rid,
                            prompt_id=prompt_id, models=models, env=env_record,
                            execution=execution, outputs=outputs, error=error)
        path = run_dir / "manifest.json"
        path.write_text(mf.to_json(manifest), encoding="utf-8")
    finally:
        if started and not keep_server:
            comfy.stop()
    if error is not None:
        where = " in node %s (%s)" % (error["node"], error["node_type"]) if error.get("node") else ""
        return EXIT_TOOL_ERROR, "%s%s: %s. See %s" % (error["type"], where, error["message"],
                                                      report_path(path))
    return EXIT_OK, "OK %s" % report_path(path)


def _collect(client: comfy.Client, ex: hist.Execution, wf: wfl.Workflow, run_dir: Path) -> List[Dict[str, Any]]:
    outputs = []
    for name, local, f in mf.output_names(ex.files, wf):
        data = comfy.download(client, f.query())
        (run_dir / local).write_bytes(data)
        outputs.append({"name": name, "path": report_path(run_dir / local),
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return outputs


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
    s = sub.add_parser("server", help="start/stop/status of the pipeline's ComfyUI")
    s.add_argument("action", choices=("start", "stop", "status"))
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "run":
            pairs = ["%s=%s" % (name, value) for name, value in args.param]
            code, message = run(args.workflow, pairs, Path(os.path.abspath(args.out)), args.keep_server)
        else:
            code, message = server(args.action)
    except (ToolError, gen_rules.ConventionError) as exc:
        code, message = EXIT_TOOL_ERROR, str(exc)
    print("artpipe gen: %s" % message, file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
