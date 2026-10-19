"""uv.overlap: UV0 faces don't overlap each other.

On mobile, overlapping UV0 is usually a FEATURE: mirrored or stacked shells
(both sides of a car, identical crates) share texture pixels and double
the texel density for free. Overlap only breaks things that need a unique
pixel per surface point: lightmaps (Unity generates UV1 for those on
import), baked AO, unique decals.

So severity follows the class rule uv.overlap_allowed:
  allowed (default) -> severity info: reported for awareness, never blocks
  not allowed       -> severity error
"""
from __future__ import annotations

from maya import cmds

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import mesh_fn, mesh_shape, plural, uv0_set

ID = "uv.overlap"
SEVERITY = Severity.ERROR  # when overlap is not allowed; see run()
# Shown instead of SEVERITY in docs/conventions.md (validate/maya/gen_docs.py).
SEVERITY_DOC = "info (error where the class sets overlap_allowed: false)"
FIXABLE = False
DESCRIPTION = "UV0 faces don't overlap (reported as info where the class allows overlap)."


def _overlapping_faces(mesh: str, uv_set: str):
    """Faces of `mesh` that overlap in `uv_set`, as full-path names.

    polyUVOverlap only works on the CURRENT UV set and has no flag to pick
    another one. If UV0 isn't current, switch to it and ALWAYS switch back,
    so the check still leaves the scene exactly as it found it.
    """
    shape = mesh_shape(mesh)
    current = cmds.polyUVSet(shape, query=True, currentUVSet=True)[0]
    if current != uv_set:
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
    try:
        found = cmds.polyUVOverlap(mesh + ".f[*]", overlappingComponents=True) or []
    finally:
        if current != uv_set:
            cmds.polyUVSet(shape, currentUVSet=True, uvSet=current)
    # Returned as "SM_CrateShape.f[0:3]": expand ranges, re-attach to the transform path.
    return [mesh + c[c.index("."):] for c in cmds.ls(found, flatten=True)]


def run(ctx) -> CheckResult:
    allowed = ctx.asset_class.uv.overlap_allowed
    severity = Severity.INFO if allowed else SEVERITY
    uv_set = uv0_set(ctx)

    faces, affected, skipped = [], 0, 0
    for mesh in ctx.meshes:
        if uv_set not in mesh_fn(mesh).getUVSetNames():
            skipped += 1
            continue
        found = _overlapping_faces(mesh, uv_set)
        faces += found
        affected += bool(found)

    passed = not faces
    if passed:
        message = "no overlapping %s faces" % uv_set
    else:
        message = "%s overlap in %s on %s" % (plural(len(faces), "face"), uv_set,
                                              plural(affected, "mesh", "meshes"))
        message += (" (allowed for %s: stacked/mirrored UVs save texture space)" if allowed
                    else " (not allowed for %s)") % ctx.asset_class.name
    if skipped:
        message += "; %s skipped, no %s set (see uv.present)" % (plural(skipped, "mesh", "meshes"), uv_set)
    return ctx.result(ID, severity, FIXABLE, passed=passed, message=message,
                      metrics={"overlap_allowed": allowed, "overlapping_faces": len(faces),
                               "meshes_affected": affected, "meshes_skipped": skipped},
                      offenders=faces)
