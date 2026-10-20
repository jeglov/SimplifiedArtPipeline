"""Workflow files: a ComfyUI API-format graph plus what the pipeline may change.

gen/workflows/<id>.json:
    {
      "id": "smoke_empty_image",
      "description": "...",
      "models": ["z_image_turbo"],               # ids from conventions/generation.yaml
      "params": {"seed": {"node": "3", "input": "seed", "type": "int", "default": 0}},
      "outputs": {"image": "9"},                 # name -> save node id
      "graph": {"3": {"class_type": "KSampler", "inputs": {...}}, ...}
    }

The graph is ComfyUI's API format (what POST /prompt takes), not the UI
workflow JSON: export it from the ComfyUI menu with "Export (API)". Only the
inputs listed in `params` can change between runs, so a run is fully described
by (workflow file, params), and the run id is a hash of everything that
changes the output.

Pure Python 3.9.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from gen.core import ToolError
from gen.core.rules import GenerationRules, ModelPin

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / "workflows"
PARAM_TYPES = {"int": int, "float": float, "str": str}
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
# String inputs that name a model file. Any such value in the graph must be a
# declared, pinned model: an undeclared file would run unhashed.
MODEL_EXTENSIONS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")
_KEYS = {"id", "description", "models", "params", "outputs", "graph"}
_PARAM_KEYS = {"node", "input", "type", "default"}


@dataclass(frozen=True)
class Param:
    name: str
    node: str
    input: str
    type: str
    default: Any


@dataclass(frozen=True)
class Workflow:
    id: str
    path: Path
    sha256: str
    description: str
    models: Tuple[str, ...]
    params: Dict[str, Param]
    outputs: Dict[str, str]  # output name -> save node id
    graph: Dict[str, Any]


def canonical_json(data: Any) -> str:
    """One byte layout per value: the basis of every hash and run id."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_type(value: Any, typ: str) -> bool:
    if isinstance(value, bool):  # bool is an int subclass: never a valid number here
        return False
    if typ == "float":
        return isinstance(value, (int, float))
    return isinstance(value, PARAM_TYPES[typ])


def parse(data: Any, path: Path, sha256: str, rules: GenerationRules) -> Workflow:
    where = path.name

    def fail(msg: str) -> ToolError:
        return ToolError("%s: %s" % (where, msg))

    if not isinstance(data, dict):
        raise fail("top level must be an object")
    unknown = sorted(set(data) - _KEYS)
    missing = sorted(_KEYS - set(data))
    if unknown or missing:
        raise fail("unknown keys %s, missing keys %s" % (unknown, missing))
    wid = data["id"]
    if not isinstance(wid, str) or not _ID.match(wid) or wid != path.stem:
        raise fail("id must be snake_case and equal the file name (%r)" % path.stem)
    graph = data["graph"]
    if not isinstance(graph, dict) or not graph:
        raise fail("graph must be a non-empty object (ComfyUI API format)")
    for nid, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) \
                or not isinstance(node.get("inputs"), dict):
            raise fail("graph node %r needs class_type and inputs (is this the UI format? "
                       "use Export (API))" % nid)

    models = data["models"]
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise fail("models must be a list of model ids")
    for mid in models:
        if mid not in rules.models:
            raise fail("model %r is not pinned in conventions/generation.yaml" % mid)
    declared = {rules.models[m].file for m in models}
    used = sorted(model_files(graph))
    undeclared = [f for f in used if f not in declared]
    if undeclared:
        raise fail("graph loads model files not declared in `models`: %s" % undeclared)

    params = {}
    if not isinstance(data["params"], dict):
        raise fail("params must be an object")
    for name, spec in sorted(data["params"].items()):
        if not _ID.match(name) or not isinstance(spec, dict) or set(spec) != _PARAM_KEYS:
            raise fail("param %r must be snake_case with keys %s" % (name, sorted(_PARAM_KEYS)))
        if spec["type"] not in PARAM_TYPES:
            raise fail("param %r: type must be one of %s" % (name, sorted(PARAM_TYPES)))
        node = graph.get(spec["node"])
        if node is None or spec["input"] not in node["inputs"]:
            raise fail("param %r points at %s.%s, which is not in the graph"
                       % (name, spec["node"], spec["input"]))
        if not _check_type(spec["default"], spec["type"]):
            raise fail("param %r: default is not a %s" % (name, spec["type"]))
        params[name] = Param(name, spec["node"], spec["input"], spec["type"], spec["default"])

    outputs = data["outputs"]
    if not isinstance(outputs, dict) or not outputs:
        raise fail("outputs must name at least one save node")
    for name, nid in sorted(outputs.items()):
        if not _ID.match(name) or nid not in graph:
            raise fail("output %r: snake_case name and an existing node id required" % name)
        if "filename_prefix" not in graph[nid]["inputs"]:
            raise fail("output %r: node %s has no filename_prefix (not a save node?)" % (name, nid))

    return Workflow(wid, path, sha256, str(data["description"]), tuple(models), params,
                    dict(outputs), graph)


def load(path: Path, rules: GenerationRules) -> Workflow:
    """Load gen/workflows/<id>.json, or a path to any workflow file."""
    path = Path(path)
    if not path.suffix and not path.exists():
        path = WORKFLOW_DIR / (str(path) + ".json")
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise ToolError("cannot read workflow %s: %s" % (path, exc))
    except ValueError as exc:
        raise ToolError("%s: not valid JSON: %s" % (path.name, exc))
    return parse(data, path, hashlib.sha256(raw).hexdigest(), rules)


def model_files(graph: Mapping[str, Any]) -> List[str]:
    """Every string input in the graph that looks like a model file name."""
    found = set()
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS):
                found.add(value.replace("\\", "/").split("/")[-1])
    return sorted(found)


def parse_overrides(pairs: List[str], wf: Workflow) -> Dict[str, Any]:
    """CLI "name=value" pairs -> typed values. Unknown names are an error, not ignored."""
    out = {}
    for pair in pairs:
        name, sep, text = pair.partition("=")
        if not sep:
            raise ToolError("--param %r: expected name=value" % pair)
        p = wf.params.get(name)
        if p is None:
            raise ToolError("--param %r: workflow %s has params %s" % (name, wf.id, sorted(wf.params)))
        try:
            out[name] = PARAM_TYPES[p.type](text)
        except ValueError:
            raise ToolError("--param %s=%r: not a %s" % (name, text, p.type))
    return out


def resolve_params(wf: Workflow, overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Defaults + overrides, every value type-checked. All params end up explicit."""
    unknown = sorted(set(overrides) - set(wf.params))
    if unknown:
        raise ToolError("unknown params %s for workflow %s" % (unknown, wf.id))
    values = {}
    for name, p in sorted(wf.params.items()):
        value = overrides.get(name, p.default)
        if not _check_type(value, p.type):
            raise ToolError("param %s=%r is not a %s" % (name, value, p.type))
        values[name] = float(value) if p.type == "float" else value
    return values


def fill(wf: Workflow, values: Mapping[str, Any]) -> Dict[str, Any]:
    """The graph with params applied. Never mutates the workflow."""
    graph = copy.deepcopy(wf.graph)
    for name, value in values.items():
        p = wf.params[name]
        graph[p.node]["inputs"][p.input] = value
    return graph


def run_id(wf: Workflow, graph: Mapping[str, Any], models: Mapping[str, str], comfy_commit: str) -> str:
    """<workflow>-<12 hex>: a hash of everything that changes the output.

    The filled graph (params, seeds, prompts, model file names), the sha256 of
    every model file and the ComfyUI commit. Not the GPU, driver or torch
    build: they change pixels too, but a run id is "what was asked", and they
    are recorded in the manifest instead. Not the output prefixes: they are
    derived from the run id.
    """
    key = canonical_json({"workflow": wf.id, "graph": graph, "models": dict(models),
                          "comfyui": comfy_commit})
    return "%s-%s" % (wf.id, sha256_text(key)[:12])


def with_prefixes(wf: Workflow, graph: Mapping[str, Any], rid: str) -> Dict[str, Any]:
    """Point every save node at artpipe/<run id>/<output name>: unique per run,
    so ComfyUI's per-prefix counter never mixes runs."""
    graph = copy.deepcopy(dict(graph))
    for name, nid in wf.outputs.items():
        graph[nid]["inputs"]["filename_prefix"] = "artpipe/%s/%s" % (rid, name)
    return graph


def pins_for(wf: Workflow, rules: GenerationRules) -> List[ModelPin]:
    return [rules.models[m] for m in sorted(set(wf.models))]
