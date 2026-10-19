"""Load and validate the conventions/ folder into typed, immutable objects.

Why a strict loader instead of passing raw dicts around:
  * Typos fail at load time with a precise path ("classes.prop_small.tri_max:
    unknown key"), not as a silently-ignored rule in production.
  * Checks get typed attributes (cls.tris_max) instead of nested .get() calls
    with scattered defaults.
  * Cross-file consistency (texture sizes per class, texture suffixes vs
    naming schema) is verified in one place.

Hand-rolled validation instead of the `jsonschema` package: it pulls in a
Rust extension (rpds-py) we'd have to install into mayapy. These configs are
small and the checks below are explicit.

Pure Python (no maya import). Uses yaml.safe_load only: yaml.load can
construct arbitrary Python objects from a crafted file.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import yaml

from validate.core.naming import NamingRules
from validate.core.result import Severity

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "conventions"
SUPPORTED_VERSION = 1
PIVOT_MODES = ("bottom_center", "center", "origin")
MESH_TYPES = ("SM", "SK")
# Unity build targets the texture import hints may name, and the GPU formats
# they may ask for. ASTC block size = pixels per 128-bit block: 4x4 is
# 8 bits/pixel (finest), 6x6 3.56 bpp, 8x8 2 bpp, 12x12 0.89 bpp.
TEXTURE_PLATFORMS = ("android", "ios")
TEXTURE_FORMATS = ("ASTC_4x4", "ASTC_5x5", "ASTC_6x6", "ASTC_8x8", "ASTC_10x10", "ASTC_12x12")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class ConventionError(ValueError):
    """A conventions file is malformed. Message starts with the file and key path."""


# --- typed views ----------------------------------------------------------------

@dataclass(frozen=True)
class LodRules:
    min: int
    max: int
    max_ratio: Tuple[float, ...]  # index = LOD level, [0] == 1.0
    tolerance: float

    def max_tris(self, level: int, lod0_tris: int) -> int:
        """Max tris allowed for LOD<level> given the actual LOD0 count."""
        return int(lod0_tris * self.max_ratio[level] * (1.0 + self.tolerance))


@dataclass(frozen=True)
class PivotRules:
    mode: str
    tolerance_cm: float


@dataclass(frozen=True)
class UVRules:
    required_sets: Tuple[str, ...]
    unit_square: bool
    overlap_allowed: bool


@dataclass(frozen=True)
class AssetClass:
    name: str
    description: str
    mesh_types: Tuple[str, ...]
    tris_max: int
    size_cm_min: float
    size_cm_max: float
    lods: LodRules
    pivot: PivotRules
    max_materials: int
    uv: UVRules
    texture_max_size: int
    severity_overrides: Tuple[Tuple[str, Severity], ...]  # sorted, hashable

    def severity_for(self, check_id: str, default: Severity) -> Severity:
        """Effective severity of a check for this class (override or default)."""
        return dict(self.severity_overrides).get(check_id, default)


@dataclass(frozen=True)
class TextureType:
    suffix: str
    description: str
    color_space: str
    alpha: str
    channels: Tuple[Tuple[str, str], ...]
    # GPU format per platform for this map type: import_hints defaults with
    # the type's override already applied, e.g. (("android", "ASTC_4x4"), ...).
    formats: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class TextureRules:
    source_formats: Tuple[str, ...]
    power_of_two: bool
    square_required: bool
    min_size: int
    types: Tuple[TextureType, ...]
    mipmaps: bool


@dataclass(frozen=True)
class FbxExportRules:
    """conventions/fbx_export.yaml. `flags` holds every on/off exporter setting,
    sorted by name, so consumers can't silently miss a new one."""
    settings_version: int
    file_version: str
    up_axis: str
    flags: Tuple[Tuple[str, bool], ...]

    def flag(self, name: str) -> bool:
        return dict(self.flags)[name]


# Every boolean in fbx_export.yaml. Required, so adding a setting to the
# Maya exporter without deciding its value here is a load error.
FBX_FLAGS = (
    "animation", "ascii", "blend_shapes", "cameras", "constraints", "embed_textures",
    "hard_edges", "input_connections", "instances", "lights", "referenced_assets_content",
    "skeleton_definitions", "skins", "smooth_mesh", "smoothing_groups", "tangents", "triangulate",
)


@dataclass(frozen=True)
class UnityImportRules:
    """conventions/unity_import.yaml: how the Unity package imports assets."""
    root: str  # e.g. "Assets/Art": the only folder the importer manages


@dataclass(frozen=True)
class Conventions:
    linear_unit: str
    classes: Tuple[AssetClass, ...]
    textures: TextureRules
    naming: NamingRules
    fbx_export: FbxExportRules
    unity_import: UnityImportRules

    @property
    def class_names(self) -> Tuple[str, ...]:
        return tuple(c.name for c in self.classes)

    def asset_class(self, name: str) -> AssetClass:
        for c in self.classes:
            if c.name == name:
                return c
        # Agent-friendly: list the valid options so it can self-correct.
        raise KeyError("unknown asset class %r, expected one of %s" % (name, list(self.class_names)))


# --- loading helpers ------------------------------------------------------------

def _deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> Dict[str, Any]:
    """Dicts merge recursively; scalars and lists in `over` replace `base`."""
    out = copy.deepcopy(dict(base))
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class _Reader:
    """Typed accessor that fails with the full key path on any mistake."""

    def __init__(self, data: Any, where: str) -> None:
        if not isinstance(data, dict):
            raise ConventionError("%s: expected a mapping" % where)
        self.data, self.where = data, where
        self._used = set()

    def get(self, key: str, typ: Any, required: bool = True, default: Any = None) -> Any:
        self._used.add(key)
        if key not in self.data:
            if required:
                raise ConventionError("%s.%s: missing required key" % (self.where, key))
            return default
        val = self.data[key]
        # bool is a subclass of int: reject `true` where a number is expected.
        if typ in (int, float) and isinstance(val, bool):
            raise ConventionError("%s.%s: expected %s, got bool" % (self.where, key, typ.__name__))
        if typ is float and isinstance(val, int):
            val = float(val)
        if not isinstance(val, typ):
            raise ConventionError("%s.%s: expected %s, got %s" % (
                self.where, key, getattr(typ, "__name__", typ), type(val).__name__))
        return val

    def sub(self, key: str) -> "_Reader":
        return _Reader(self.get(key, dict), "%s.%s" % (self.where, key))

    def done(self) -> None:
        """Call after reading: any key never asked for is a typo."""
        unknown = sorted(set(self.data) - self._used)
        if unknown:
            raise ConventionError("%s: unknown key(s) %s" % (self.where, unknown))


def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConventionError("%s: top level must be a mapping" % path.name)
    if data.get("version") != SUPPORTED_VERSION:
        raise ConventionError("%s: version must be %d" % (path.name, SUPPORTED_VERSION))
    return data


def _parse_class(name: str, raw: Dict[str, Any], tex_sizes: Dict[str, int], where: str) -> AssetClass:
    r = _Reader(raw, where)
    description = r.get("description", str)

    mesh_types = tuple(r.get("mesh_types", list))
    if not mesh_types or any(t not in MESH_TYPES for t in mesh_types):
        raise ConventionError("%s.mesh_types: must be a non-empty subset of %s" % (where, MESH_TYPES))

    tris_max = r.get("tris_max", int)
    if tris_max <= 0:
        raise ConventionError("%s.tris_max: must be > 0" % where)

    size = r.sub("size_cm")
    smin, smax = size.get("min", float), size.get("max", float)
    size.done()
    if not 0 < smin < smax:
        raise ConventionError("%s.size_cm: need 0 < min < max" % where)

    lr = r.sub("lods")
    lods = LodRules(lr.get("min", int), lr.get("max", int),
                    tuple(float(x) for x in lr.get("max_ratio", list)), lr.get("tolerance", float))
    lr.done()
    ratios = lods.max_ratio
    if not 1 <= lods.min <= lods.max <= len(ratios):
        raise ConventionError("%s.lods: need 1 <= min <= max <= len(max_ratio)" % where)
    if ratios[0] != 1.0 or any(b > a for a, b in zip(ratios, ratios[1:])):
        raise ConventionError("%s.lods.max_ratio: must start at 1.0 and never increase" % where)

    pr = r.sub("pivot")
    pivot = PivotRules(pr.get("mode", str), pr.get("tolerance_cm", float))
    pr.done()
    if pivot.mode not in PIVOT_MODES:
        raise ConventionError("%s.pivot.mode: must be one of %s" % (where, PIVOT_MODES))

    max_materials = r.get("max_materials", int)

    ur = r.sub("uv")
    uv = UVRules(tuple(ur.get("required_sets", list)), ur.get("unit_square", bool),
                 ur.get("overlap_allowed", bool))
    ur.done()

    overrides = []
    for check_id, sev in sorted(r.get("severity_overrides", dict).items()):
        # Format only; existence of the check ID is verified against the
        # registry in P1.3 (docs/backlog.md #5).
        if not _CHECK_ID.match(check_id):
            raise ConventionError("%s.severity_overrides: bad check id %r" % (where, check_id))
        try:
            overrides.append((check_id, Severity(sev)))
        except ValueError:
            raise ConventionError("%s.severity_overrides.%s: bad severity %r" % (where, check_id, sev))
    r.done()

    return AssetClass(
        name=name, description=description, mesh_types=mesh_types, tris_max=tris_max,
        size_cm_min=smin, size_cm_max=smax, lods=lods, pivot=pivot,
        max_materials=max_materials, uv=uv,
        texture_max_size=tex_sizes.get(name, tex_sizes["default"]),
        severity_overrides=tuple(overrides),
    )


def _is_pot(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _parse_formats(raw: Dict[str, Any], where: str) -> Dict[str, str]:
    """{platform: {format: X}} -> {platform: X}, validated."""
    if not isinstance(raw, dict):
        raise ConventionError("%s: expected a mapping" % where)
    out = {}
    for platform, spec in sorted(raw.items()):
        if platform not in TEXTURE_PLATFORMS:
            raise ConventionError("%s.%s: unknown platform, expected one of %s"
                                  % (where, platform, TEXTURE_PLATFORMS))
        pr = _Reader(spec, "%s.%s" % (where, platform))
        fmt = pr.get("format", str)
        pr.done()
        if fmt not in TEXTURE_FORMATS:
            raise ConventionError("%s.%s.format: %r is not one of %s"
                                  % (where, platform, fmt, TEXTURE_FORMATS))
        out[platform] = fmt
    return out


def _parse_import_hints(raw: Dict[str, Any], suffixes: Tuple[str, ...]
                        ) -> Tuple[bool, Dict[str, Dict[str, str]]]:
    """(mipmaps, {suffix: {platform: format}}) with overrides resolved."""
    where = "texture_rules.import_hints"
    hr = _Reader(raw, where)
    mipmaps = hr.get("mipmaps", bool)
    defaults = _parse_formats({p: hr.get(p, dict) for p in TEXTURE_PLATFORMS}, where)
    overrides = hr.get("overrides", dict, required=False, default={})
    hr.done()
    unknown = sorted(set(overrides) - set(suffixes))
    if unknown:
        raise ConventionError("%s.overrides: unknown map type(s) %s" % (where, unknown))
    resolved = {}
    for suffix in suffixes:
        formats = dict(defaults)
        formats.update(_parse_formats(overrides.get(suffix) or {}, "%s.overrides.%s" % (where, suffix)))
        resolved[suffix] = formats
    return mipmaps, resolved


def _parse_textures(data: Dict[str, Any]) -> Tuple[TextureRules, Dict[str, int]]:
    r = _Reader(data, "texture_rules")
    r.get("version", int)
    sizes = r.get("max_size_by_class", dict)
    if "default" not in sizes:
        raise ConventionError("texture_rules.max_size_by_class: missing 'default'")
    for k, v in sizes.items():
        if not isinstance(v, int) or isinstance(v, bool) or not _is_pot(v):
            raise ConventionError("texture_rules.max_size_by_class.%s: must be a power of two" % k)

    raw_types = r.get("types", dict)
    mipmaps, formats = _parse_import_hints(r.get("import_hints", dict), tuple(sorted(raw_types)))
    types = []
    for suffix, raw in sorted(raw_types.items()):
        tr = _Reader(raw, "texture_rules.types.%s" % suffix)
        tt = TextureType(
            suffix=suffix,
            description=tr.get("description", str),
            color_space=tr.get("color_space", str),
            alpha=tr.get("alpha", str),
            channels=tuple(sorted(tr.get("channels", dict).items())),
            formats=tuple(sorted(formats[suffix].items())),
        )
        tr.get("normal_convention", str, required=False)  # informational
        tr.done()
        if tt.color_space not in ("srgb", "linear"):
            raise ConventionError("texture_rules.types.%s.color_space: srgb|linear" % suffix)
        if tt.alpha not in ("optional", "required", "forbidden"):
            raise ConventionError("texture_rules.types.%s.alpha: optional|required|forbidden" % suffix)
        types.append(tt)

    rules = TextureRules(
        source_formats=tuple(r.get("source_formats", list)),
        power_of_two=r.get("power_of_two", bool),
        square_required=r.get("square_required", bool),
        min_size=r.get("min_size", int),
        types=tuple(types),
        mipmaps=mipmaps,
    )
    r.done()
    return rules, sizes


def _parse_fbx_export(data: Dict[str, Any]) -> FbxExportRules:
    r = _Reader(data, "fbx_export")
    r.get("version", int)
    settings_version = r.get("settings_version", int)
    if settings_version < 1:
        raise ConventionError("fbx_export.settings_version: must be >= 1")
    file_version = r.get("file_version", str)
    if not re.match(r"^FBX20[0-9]{4}$", file_version):
        raise ConventionError("fbx_export.file_version: expected e.g. FBX202000, got %r" % file_version)
    up_axis = r.get("up_axis", str)
    if up_axis not in ("y", "z"):
        raise ConventionError("fbx_export.up_axis: must be y or z")
    flags = tuple((name, r.get(name, bool)) for name in FBX_FLAGS)
    r.done()
    if not dict(flags)["ascii"]:
        # The canonicalizer only understands ASCII: binary output would make
        # every export unique and leak absolute paths.
        raise ConventionError("fbx_export.ascii: must be true (deterministic export needs ASCII FBX)")
    return FbxExportRules(settings_version, file_version, up_axis, flags)


def _parse_unity_import(data: Dict[str, Any]) -> UnityImportRules:
    r = _Reader(data, "unity_import")
    r.get("version", int)
    root = r.get("root", str)
    r.done()
    # Unity asset paths: forward slashes, under Assets/, no trailing slash.
    if not re.match(r"^Assets(/[^/\\]+)+$", root):
        raise ConventionError("unity_import.root: expected a folder like Assets/Art, got %r" % root)
    return UnityImportRules(root=root)


def load(conventions_dir: Optional[Path] = None) -> Conventions:
    """Load, validate and cross-check all convention files."""
    d = Path(conventions_dir) if conventions_dir else DEFAULT_DIR

    with (d / "naming.schema.json").open("r", encoding="utf-8") as fh:
        naming = NamingRules(json.load(fh))

    textures, tex_sizes = _parse_textures(_read_yaml(d / "texture_rules.yaml"))
    fbx_export = _parse_fbx_export(_read_yaml(d / "fbx_export.yaml"))
    unity_import = _parse_unity_import(_read_yaml(d / "unity_import.yaml"))

    budgets = _read_yaml(d / "mesh_budgets.yaml")
    br = _Reader(budgets, "mesh_budgets")
    br.get("version", int)
    ur = br.sub("units")
    linear_unit = ur.get("linear", str)
    ur.done()
    defaults = br.get("defaults", dict)
    raw_classes = br.get("classes", dict)
    br.done()

    classes = tuple(
        _parse_class(name, _deep_merge(defaults, raw or {}), tex_sizes, "mesh_budgets.classes.%s" % name)
        for name, raw in sorted(raw_classes.items())  # sorted = deterministic order
    )

    # --- cross-file consistency ---
    class_names = {c.name for c in classes}
    stray = sorted(set(tex_sizes) - class_names - {"default"})
    if stray:
        raise ConventionError("texture_rules.max_size_by_class: unknown classes %s" % stray)

    # The suffix list lives in the texture_file pattern as "_(D|N|MS|AO|E)".
    # Extract it rather than duplicating it in a second key that could drift.
    suffix_group = re.search(r"_\(([A-Z|]+)\)", naming.rule("texture_file").pattern.pattern)
    schema_suffixes = set(suffix_group.group(1).split("|")) if suffix_group else set()
    rule_suffixes = {t.suffix for t in textures.types}
    if schema_suffixes != rule_suffixes:
        raise ConventionError("texture suffixes differ: naming.schema.json %s vs texture_rules.yaml %s"
                              % (sorted(schema_suffixes), sorted(rule_suffixes)))

    return Conventions(linear_unit=linear_unit, classes=classes, textures=textures, naming=naming,
                       fbx_export=fbx_export, unity_import=unity_import)
