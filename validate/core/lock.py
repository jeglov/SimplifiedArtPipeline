"""conventions.lock.json: the conventions, resolved, for consumers without YAML.

    artpipe conventions --dump     (re)write conventions/conventions.lock.json
    artpipe conventions --check    exit 1 if the lock is stale (also a test)

The YAML/JSON files in conventions/ stay the human-authored source of truth.
The lock is a build artefact that is committed on purpose, so Unity C# can
read the rules with no YAML package and no Python:

  * RESOLVED, not copied: defaults are merged into every class and texture
    format overrides are applied per map type. C# reads values and never
    re-implements inheritance, so the two languages can't disagree on it.
  * VALIDATED: built from the loaded `Conventions`, so a lock can only exist
    for rules that passed every Phase 1 check.
  * TRACEABLE: `sources` holds the sha256 of each source file, so a reader
    (Unity's preflight) can tell when the lock is older than the YAML.
  * DETERMINISTIC: sorted keys, LF, no timestamps. Same rules, same bytes.

Pure Python (no Maya). Exit codes: 0 ok / 1 stale lock / 2 tool error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from validate.core import conventions as conv

LOCK_VERSION = 1
LOCK_NAME = "conventions.lock.json"
SOURCES = ("fbx_export.yaml", "mesh_budgets.yaml", "naming.schema.json", "texture_rules.yaml",
           "unity_import.yaml")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(c: conv.Conventions, conventions_dir: Path) -> Dict[str, Any]:
    classes = {}
    for k in c.classes:
        classes[k.name] = {
            "description": k.description,
            "mesh_types": list(k.mesh_types),
            "tris_max": k.tris_max,
            "size_cm": {"min": k.size_cm_min, "max": k.size_cm_max},
            "lods": {"min": k.lods.min, "max": k.lods.max,
                     "max_ratio": list(k.lods.max_ratio), "tolerance": k.lods.tolerance},
            "pivot": {"mode": k.pivot.mode, "tolerance_cm": k.pivot.tolerance_cm},
            "max_materials": k.max_materials,
            "uv": {"required_sets": list(k.uv.required_sets), "unit_square": k.uv.unit_square,
                   "overlap_allowed": k.uv.overlap_allowed},
            "texture_max_size": k.texture_max_size,
            "severity_overrides": {cid: sev.value for cid, sev in k.severity_overrides},
        }
    t = c.textures
    textures = {
        "source_formats": list(t.source_formats),
        "power_of_two": t.power_of_two,
        "square_required": t.square_required,
        "min_size": t.min_size,
        "mipmaps": t.mipmaps,
        "types": {x.suffix: {"description": x.description, "color_space": x.color_space,
                             "alpha": x.alpha, "channels": dict(x.channels),
                             "formats": dict(x.formats)}
                  for x in t.types},
    }
    naming = {}
    for kind in c.naming.kinds:
        r = c.naming.rule(kind)
        naming[kind] = {"pattern": r.pattern.pattern, "description": r.description,
                        "examples": list(r.examples), "invalid_examples": list(r.invalid_examples)}
    f = c.fbx_export
    fbx_export = {"settings_version": f.settings_version, "file_version": f.file_version,
                  "up_axis": f.up_axis, "flags": dict(f.flags)}
    return {
        "lock_version": LOCK_VERSION,
        "generated_by": "artpipe conventions --dump",
        "sources": {name: _sha256(conventions_dir / name) for name in SOURCES},
        "units": {"linear": c.linear_unit},
        "classes": classes,
        "textures": textures,
        "naming": naming,
        "fbx_export": fbx_export,
        "unity_import": {"root": c.unity_import.root},
    }


def to_json(lock: Dict[str, Any]) -> str:
    """Same canonical form as the reports: sorted keys, 2-space indent, LF."""
    return json.dumps(lock, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render(conventions_dir: Optional[Path] = None) -> str:
    d = Path(conventions_dir) if conventions_dir else conv.DEFAULT_DIR
    return to_json(build(conv.load(d), d))


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="artpipe conventions", description=__doc__.split("\n\n")[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dump", action="store_true", help="write %s" % LOCK_NAME)
    mode.add_argument("--check", action="store_true", help="exit 1 if %s is stale" % LOCK_NAME)
    p.add_argument("--dir", type=Path, default=conv.DEFAULT_DIR, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    try:
        text = render(args.dir)
    except (conv.ConventionError, OSError, KeyError) as exc:
        print("artpipe conventions: %s" % exc, file=sys.stderr)
        return 2
    path = args.dir / LOCK_NAME
    if args.check:
        current = path.read_bytes().decode("utf-8") if path.is_file() else None
        if current != text:
            print("artpipe conventions: %s is stale. Run: artpipe conventions --dump" % LOCK_NAME,
                  file=sys.stderr)
            return 1
        print("artpipe conventions: %s is up to date" % LOCK_NAME)
        return 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("artpipe conventions: wrote %s" % path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
