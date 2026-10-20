"""conventions/generation.yaml as typed, immutable rules.

Same strict reader as validate.core.conventions: an unknown or mistyped key
fails at load time with its full path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml, _Reader

FILE = "generation.yaml"
STYLES_FILE = "gen_styles.yaml"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ComfyRules:
    version: str
    commit: str
    port: int
    startup_timeout_s: int
    run_timeout_s: int


@dataclass(frozen=True)
class ModelPin:
    id: str
    folder: str    # ComfyUI model folder: diffusion_models, vae, text_encoders...
    file: str
    sha256: str
    licence: str
    source: str


@dataclass(frozen=True)
class ConceptRules:
    """Thresholds of the concept image checks (gen.core.concept)."""
    grid: int
    mask_threshold: int
    background_tolerance: int
    background_uniformity_min: float
    coverage_min: float
    coverage_max: float
    margin_min: float
    single_object_min: float
    speck_cells: int


@dataclass(frozen=True)
class ModelRules:
    """Image-to-3D settings (gen.cli model)."""
    face_budget_share: float
    remesh_resolution: int
    uv_overlap_max_share: float

    def target_faces(self, tris_max: int) -> int:
        return max(1, int(tris_max * self.face_budget_share))


@dataclass(frozen=True)
class IngestRules:
    size_cm: Dict[str, float]  # largest dimension per asset class


@dataclass(frozen=True)
class RepairRules:
    max_attempts: int
    seed_step: int
    default: str
    actions: Dict[str, str]


@dataclass(frozen=True)
class GenerationRules:
    comfyui: ComfyRules
    models: Dict[str, ModelPin]
    concept: ConceptRules
    model: ModelRules
    ingest: IngestRules
    repair: RepairRules


@dataclass(frozen=True)
class Style:
    id: str
    description: str
    prompt: str  # contains {subject} exactly once


@dataclass(frozen=True)
class StyleRules:
    framing: str
    classes: Dict[str, str]
    styles: Dict[str, Style]

    def prompt(self, subject: str, asset_class: str, style: str) -> str:
        """The full concept prompt. Unknown class or style: ConventionError listing the options."""
        if style not in self.styles:
            raise ConventionError("unknown style %r, expected one of %s" % (style, sorted(self.styles)))
        if asset_class not in self.classes:
            raise ConventionError("no concept framing for class %r (gen_styles.yaml classes: %s)"
                                  % (asset_class, sorted(self.classes)))
        subject = " ".join(subject.split())
        if not subject or len(subject) > 300 or "{" in subject or "}" in subject:
            raise ConventionError("subject must be 1-300 characters without braces")
        return " ".join((self.styles[style].prompt.replace("{subject}", subject),
                         self.classes[asset_class], self.framing))


def _positive(r: _Reader, key: str) -> int:
    val = r.get(key, int)
    if val <= 0:
        raise ConventionError("%s.%s: must be > 0, got %d" % (r.where, key, val))
    return val


def parse(data: dict) -> GenerationRules:
    root = _Reader(data, FILE)
    root.get("version", int)
    c = root.sub("comfyui")
    commit = c.get("commit", str)
    if not _COMMIT.match(commit):
        raise ConventionError("%s.comfyui.commit: expected a 40-char git hash" % FILE)
    port = _positive(c, "port")
    if port > 65535:
        raise ConventionError("%s.comfyui.port: must be <= 65535, got %d" % (FILE, port))
    comfy = ComfyRules(c.get("version", str), commit, port,
                       _positive(c, "startup_timeout_s"), _positive(c, "run_timeout_s"))
    c.done()

    models = {}
    raw_models = root.get("models", dict)
    for mid in sorted(raw_models):
        where = "%s.models.%s" % (FILE, mid)
        if not _MODEL_ID.match(str(mid)):
            raise ConventionError("%s: model id must be snake_case" % where)
        m = _Reader(raw_models[mid], where)
        pin = ModelPin(mid, m.get("folder", str), m.get("file", str), m.get("sha256", str),
                       m.get("licence", str), m.get("source", str))
        m.done()
        if not _SHA256.match(pin.sha256):
            raise ConventionError("%s.sha256: expected 64 lowercase hex chars" % where)
        if "/" in pin.file or "\\" in pin.file or "/" in pin.folder or "\\" in pin.folder:
            raise ConventionError("%s: folder and file must be plain names" % where)
        models[mid] = pin
    k = root.sub("concept")
    concept = ConceptRules(
        _positive(k, "grid"), _byte(k, "mask_threshold"), _positive(k, "background_tolerance"),
        _share(k, "background_uniformity_min"), _share(k, "coverage_min"), _share(k, "coverage_max"),
        _share(k, "margin_min"), _share(k, "single_object_min"), _positive(k, "speck_cells"))
    k.done()
    if concept.coverage_min >= concept.coverage_max:
        raise ConventionError("%s.concept: coverage_min must be < coverage_max" % FILE)
    mr = root.sub("model")
    model = ModelRules(_share(mr, "face_budget_share"), _positive(mr, "remesh_resolution"),
                       _share(mr, "uv_overlap_max_share"))
    mr.done()
    if model.face_budget_share == 0:
        raise ConventionError("%s.model.face_budget_share: must be > 0" % FILE)
    ir = root.sub("ingest")
    sizes = {}
    for name, value in sorted(ir.get("size_cm", dict).items()):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConventionError("%s.ingest.size_cm.%s: expected a size > 0" % (FILE, name))
        sizes[name] = float(value)
    ir.done()
    rr = root.sub("repair")
    repair = RepairRules(_positive(rr, "max_attempts"), _positive(rr, "seed_step"),
                         _action(rr.where + ".default", rr.get("default", str)), {})
    for cid, action in sorted(rr.get("actions", dict).items()):
        if not _CHECK_ID.match(str(cid)):
            raise ConventionError("%s.actions.%s: not a check id (category.name)" % (rr.where, cid))
        repair.actions[cid] = _action("%s.actions.%s" % (rr.where, cid), action)
    rr.done()
    root.done()
    return GenerationRules(comfy, models, concept, model, IngestRules(sizes), repair)


def _action(where: str, value) -> str:
    if value not in ("retry", "stop"):
        raise ConventionError("%s: expected retry or stop, got %r" % (where, value))
    return value


def _byte(r: _Reader, key: str) -> int:
    val = r.get(key, int)
    if not 1 <= val <= 255:
        raise ConventionError("%s.%s: must be 1-255, got %d" % (r.where, key, val))
    return val


def _share(r: _Reader, key: str) -> float:
    val = r.get(key, float)
    if not 0.0 <= val <= 1.0:
        raise ConventionError("%s.%s: must be between 0 and 1, got %s" % (r.where, key, val))
    return val


def parse_styles(data: dict, class_names=None) -> StyleRules:
    where = STYLES_FILE
    root = _Reader(data, where)
    root.get("version", int)
    framing = " ".join(root.get("framing", str).split())
    classes = {}
    raw_classes = root.get("classes", dict)
    for name in sorted(raw_classes):
        if class_names is not None and name not in class_names:
            raise ConventionError("%s.classes.%s: not an asset class in mesh_budgets.yaml" % (where, name))
        if not isinstance(raw_classes[name], str) or not raw_classes[name].strip():
            raise ConventionError("%s.classes.%s: expected a sentence" % (where, name))
        classes[name] = " ".join(raw_classes[name].split())
    styles = {}
    for sid, raw in sorted(root.get("styles", dict).items()):
        if not _MODEL_ID.match(str(sid)):
            raise ConventionError("%s.styles.%s: style id must be snake_case" % (where, sid))
        r = _Reader(raw, "%s.styles.%s" % (where, sid))
        prompt = " ".join(r.get("prompt", str).split())
        style = Style(sid, r.get("description", str), prompt)
        r.done()
        if prompt.count("{subject}") != 1 or prompt.replace("{subject}", "").count("{"):
            raise ConventionError("%s.styles.%s.prompt: needs {subject} exactly once and no other braces"
                                  % (where, sid))
        styles[sid] = style
    if not styles:
        raise ConventionError("%s: define at least one style" % where)
    root.done()
    return StyleRules(framing, classes, styles)


def load(conventions_dir: Optional[Path] = None) -> GenerationRules:
    return parse(_read_yaml((conventions_dir or DEFAULT_DIR) / FILE))


def load_styles(conventions_dir: Optional[Path] = None, class_names=None) -> StyleRules:
    return parse_styles(_read_yaml((conventions_dir or DEFAULT_DIR) / STYLES_FILE), class_names)
