"""Build the golden FBX + report pairs the Unity EditMode tests import.

    artpipe unity-fixtures

Runs the real pipeline: build the Maya fixture scenes, validate (with --fix),
export canonical FBX plus its sidecar report into the importer package's
Tests/Editor/Fixtures~/ folder. Unity never imports a folder whose name ends
in "~", so the files sit there inert until a test copies them into Assets/.

The output is committed. That only works because the whole chain is
deterministic: fixture scenes (volatile lines stripped), fixed .ma files and
canonical FBX all have stable bytes. tests/integration/test_unity_fixtures.py
regenerates them and fails if the committed copies are stale.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "unity" / "Packages" / "com.artpipe.importer" / "Tests" / "Editor" / "Fixtures~"
WORK = ROOT / "out" / "unity_fixtures"

# name -> asset class. Chosen to cover what the Unity side must handle:
# a plain prop, one that only passes after --fix, LODs, a skinned mesh, a
# centred pickup, and tiling UVs.
FIXTURES = {
    "SM_Clean": "prop_small",
    "SM_Messy": "prop_small",
    "SM_LodsGood": "prop_medium",
    "SK_Skinned": "character",
    "SM_PickupCentered": "pickup",
    "SM_UVOutsideTiling": "env_module",
}


def generate(scenes: Dict[str, Path], out_dir: Path, fix_dir: Path) -> Dict[str, Tuple[Path, Path]]:
    """{name: (fbx, report)}. Maya must be running. Raises if any fixture fails."""
    from validate.core import conventions as conv
    from validate.maya.cli import export_file, validate_file, validated_file

    c = conv.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, cls_name in sorted(FIXTURES.items()):
        cls = c.asset_class(cls_name)
        validated = validate_file(scenes[name], c, cls, max_offenders=0, fix_dir=fix_dir)
        exported = export_file(validated_file(scenes[name], validated, fix_dir), validated, c, cls,
                               out_dir, max_offenders=0)
        if exported.exit_code != 0:
            raise RuntimeError("%s did not export cleanly: %s" % (name, exported.tool_error or exported.summary()))
        report = out_dir / (name + ".fbx.report.json")
        with report.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(exported.to_json())
        made[name] = (out_dir / (name + ".fbx"), report)
    return made


def main() -> int:
    sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
    import broken_meshes
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        scenes = {k: Path(v) for k, v in broken_meshes.build_all(str(WORK / "scenes")).items()}
        for name, (fbx, report) in sorted(generate(scenes, OUT, WORK / "fixed").items()):
            print("wrote", fbx.relative_to(ROOT).as_posix(), "+ report")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("unity-fixtures: %s" % exc, file=sys.stderr)
        return 2
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    sys.exit(main())
