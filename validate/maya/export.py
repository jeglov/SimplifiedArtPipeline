"""Export the open Maya scene to a canonical ASCII FBX (Phase 2, backlog #27).

Settings come only from conventions/fbx_export.yaml. Each one is applied
after FBXResetExport and then READ BACK: a setting the plugin ignores (wrong
plugin version, renamed command) makes the export fail instead of silently
using a default. Probed on FBX plugin 2020.3.1: after FBXResetExport,
cameras, lights, blend shapes, smooth mesh and input connections default to ON.

The written file is then canonicalized (validate/core/fbx_ascii.py), and its
up axis and unit scale are checked from the FILE, not from the settings:
FBXExportConvertUnitString -q answers 1.0 instead of the unit, so a
read-back alone can't prove units.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from validate.core.conventions import Conventions, FbxExportRules
from validate.core.fbx_ascii import FbxInfo, canonicalize

# fbx_export.yaml flag -> FBX plugin MEL command (both "-v <bool>" and "-q").
FLAG_COMMANDS = {
    "ascii": "FBXExportInAscii",
    "blend_shapes": "FBXExportShapes",
    "cameras": "FBXExportCameras",
    "constraints": "FBXExportConstraints",
    "embed_textures": "FBXExportEmbeddedTextures",
    "hard_edges": "FBXExportHardEdges",
    "input_connections": "FBXExportInputConnections",
    "instances": "FBXExportInstances",
    "lights": "FBXExportLights",
    "referenced_assets_content": "FBXExportReferencedAssetsContent",
    "skeleton_definitions": "FBXExportSkeletonDefinitions",
    "skins": "FBXExportSkins",
    "smooth_mesh": "FBXExportSmoothMesh",
    "smoothing_groups": "FBXExportSmoothingGroups",
    "tangents": "FBXExportTangents",
    "triangulate": "FBXExportTriangulate",
}
# No dedicated command for these: set through the generic property tree.
FLAG_PROPERTIES = {
    "animation": "Export|IncludeGrp|Animation",
}

# FBX UnitScaleFactor is "how many cm in one file unit".
UNIT_SCALE_CM = {"mm": 0.1, "cm": 1.0, "m": 100.0}


class ExportError(RuntimeError):
    pass


def _as_bool(value: Any) -> bool:
    # The plugin answers 0/1 for most commands and "true"/"false" for others.
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


def apply_settings(rules: FbxExportRules) -> Dict[str, Any]:
    """Reset the exporter, apply every setting, read each back. Returns what was read."""
    import maya.mel as mel
    from maya import cmds

    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval("FBXResetExport")
    mel.eval("FBXExportFileVersion -v %s" % rules.file_version)
    mel.eval("FBXExportUpAxis %s" % rules.up_axis)

    flags = dict(rules.flags)
    unmapped = sorted(set(flags) - set(FLAG_COMMANDS) - set(FLAG_PROPERTIES))
    if unmapped:
        raise ExportError("fbx_export flags with no exporter command: %s" % unmapped)
    for name, value in sorted(flags.items()):
        v = "true" if value else "false"
        if name in FLAG_COMMANDS:
            mel.eval("%s -v %s" % (FLAG_COMMANDS[name], v))
        else:
            mel.eval('FBXProperty "%s" -v %s' % (FLAG_PROPERTIES[name], v))

    readback: Dict[str, Any] = {
        "file_version": mel.eval("FBXExportFileVersion -q"),
        "up_axis": mel.eval("FBXExportUpAxis -q"),
    }
    for name in sorted(flags):
        if name in FLAG_COMMANDS:
            readback[name] = _as_bool(mel.eval("%s -q" % FLAG_COMMANDS[name]))
        else:
            readback[name] = _as_bool(mel.eval('FBXProperty "%s" -q' % FLAG_PROPERTIES[name]))

    expected: Dict[str, Any] = dict(flags, file_version=rules.file_version, up_axis=rules.up_axis)
    wrong = sorted(k for k in expected if str(readback[k]).lower() != str(expected[k]).lower())
    if wrong:
        raise ExportError("FBX exporter ignored settings: %s" % ", ".join(
            "%s=%r (asked %r)" % (k, readback[k], expected[k]) for k in wrong))
    return readback


def export_fbx(path: Path, conventions: Conventions) -> FbxInfo:
    """Export the whole open scene to `path` as canonical ASCII FBX.

    Written to a temp file, canonicalized, verified, then moved into place:
    a crash never leaves a half-written or non-canonical FBX at `path`.
    """
    import maya.mel as mel

    rules = conventions.fbx_export
    apply_settings(rules)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_name(path.name + ".tmp.fbx")
    try:
        # Whole scene, no selection: cameras/lights are excluded by the
        # settings, and the naming check already guarantees one asset per file.
        mel.eval('FBXExport -f "%s"' % raw.resolve().as_posix())
        if not raw.is_file():
            raise ExportError("FBXExport wrote no file")
        text, info = canonicalize(raw.read_bytes().decode("utf-8"))
        # The canonicalizer keeps file names only; make them the FINAL name.
        text = text.replace(raw.name, path.name)
    finally:
        if raw.exists():
            raw.unlink()

    if info.up_axis != rules.up_axis:
        raise ExportError("exported FBX has up axis %r, conventions want %r" % (info.up_axis, rules.up_axis))
    want = UNIT_SCALE_CM[conventions.linear_unit]
    if abs(info.unit_scale_cm - want) > 1e-9:
        raise ExportError("exported FBX has UnitScaleFactor %s, conventions want %s (%s)"
                          % (info.unit_scale_cm, want, conventions.linear_unit))

    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(str(tmp), str(path))
    return info
