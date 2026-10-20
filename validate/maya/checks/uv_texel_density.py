"""uv.texel_density: pixels per metre at the shipped texture size, and no face far off it.

density = class texture_max_size x sqrt(UV0 area / world area in m^2)
(validate.core.texel). Fails, as a warning by default, when:
  * a mesh is below the class minimum (mesh_budgets.yaml
    uv.texel_density_min_px_per_m; 0 = no minimum). The metric
    texture_px_needed says what texture size would reach it;
  * faces are more than 2x (one mip) off the mesh's own density: blurry or
    crisp patches next to their neighbours, typical of automatic unwraps;
  * faces have no UV area at all: they show one stretched texel.

Mirrored or stacked UVs still count their full area (probed: getUVArea
ignores overlap), which is the density the player sees.
"""
from __future__ import annotations

import maya.api.OpenMaya as om

from validate.core import texel
from validate.core.result import CheckResult, Severity
from validate.maya.meshes import mesh_fn, plural, uv0_set

ID = "uv.texel_density"
SEVERITY = Severity.WARNING
FIXABLE = False
DESCRIPTION = "Texel density at the class's texture size: above the class minimum, no face 2x off, no zero-UV faces."

CM2_PER_M2 = 1e4


def face_areas(mesh: str, uv_set: str):
    """[(UV area or None, world area in m^2)] per face. Maya's internal unit is cm.

    None = the face has no UVs at all: that is uv.present's finding, not a
    density one, so it isn't reported twice.
    """
    it = om.MItMeshPolygon(mesh_fn(mesh).dagPath())
    out = []
    while not it.isDone():
        uv = it.getUVArea(uv_set) if it.hasUVs(uv_set) else None
        out.append((uv, it.getArea(om.MSpace.kWorld) / CM2_PER_M2))
        it.next()
    return out


def run(ctx) -> CheckResult:
    size = ctx.asset_class.texture_max_size
    target = ctx.asset_class.uv.texel_density_min_px_per_m
    uv_set = uv0_set(ctx)
    meshes, offenders, low, off, zero, skipped = {}, [], [], 0, 0, 0
    for mesh in ctx.meshes:
        if uv_set not in mesh_fn(mesh).getUVSetNames():
            skipped += 1
            continue
        areas = face_areas(mesh, uv_set)
        if all(uv is None for uv, _ in areas):
            skipped += 1  # no UVs on any face: uv.present reports it, once
            continue
        d = texel.density(areas, size, target)
        meshes[mesh] = {"px_per_m": round(d.px_per_m, 1), "texture_px_needed": d.texture_px_needed,
                        "off_faces": len(d.off_faces), "zero_uv_faces": len(d.zero_uv_faces)}
        if target and d.px_per_m < target:
            low.append(mesh)
            offenders.append(mesh)
        offenders += ["%s.f[%d]" % (mesh, f) for f in d.off_faces + d.zero_uv_faces]
        off += len(d.off_faces)
        zero += len(d.zero_uv_faces)

    parts = []
    if low:
        parts.append("%s below %g px/m at %d px" % (plural(len(low), "mesh", "meshes"), target, size))
    if off:
        parts.append("%s more than 2x off their mesh's density" % plural(off, "face", "faces"))
    if zero:
        parts.append("%s with no UV area" % plural(zero, "face", "faces"))
    densities = ", ".join("%s %.0f px/m" % (m.rsplit("|", 1)[-1], v["px_per_m"]) for m, v in sorted(meshes.items()))
    message = "; ".join(parts) if parts else "texel density %s at %d px" % (densities or "n/a", size)
    if skipped:
        message += "; %s skipped, no %s set (see uv.present)" % (plural(skipped, "mesh", "meshes"), uv_set)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=not offenders, message=message,
                      metrics={"texture_size": size, "min_px_per_m": target, "meshes": meshes,
                               "off_faces": off, "zero_uv_faces": zero, "meshes_skipped": skipped},
                      offenders=offenders)
