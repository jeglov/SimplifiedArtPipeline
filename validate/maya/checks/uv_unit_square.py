"""uv.unit_square: all UV0 coordinates stay inside the 0-1 square.

Mobile props usually share texture atlases: one texture holds many assets,
each in its own region. A UV outside 0-1 wraps around into a neighbour's
region (or smears the edge texels if the texture is set to Clamp). Unity
also has no UDIM support, so UDIM-style layouts from film pipelines break.

Not required for env_module (tiling textures and trim sheets go outside
0-1 on purpose): the check then passes and says so.

Meshes missing the UV0 set are skipped here; uv.present already fails them.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import component_names, mesh_fn, plural, uv0_set

ID = "uv.unit_square"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "All UV0 coordinates are inside the 0-1 square (unless the class allows tiling)."

# UVs snapped exactly to 0 or 1 come back as e.g. 1.0000001 after float maths.
EPSILON = 1e-4


def _outside(u: float, v: float) -> bool:
    return not (-EPSILON <= u <= 1 + EPSILON and -EPSILON <= v <= 1 + EPSILON)


def run(ctx) -> CheckResult:
    uv_set = uv0_set(ctx)
    if not ctx.asset_class.uv.unit_square:
        return ctx.result(ID, SEVERITY, FIXABLE, passed=True,
                          message="not required for %s (tiling UVs allowed)" % ctx.asset_class.name,
                          metrics={"required": False})

    faces, skipped = [], 0
    us_all, vs_all = [], []
    for mesh in ctx.meshes:
        fn = mesh_fn(mesh)
        if uv_set not in fn.getUVSetNames():
            skipped += 1
            continue
        us, vs = fn.getUVs(uv_set)
        us_all += us
        vs_all += vs
        bad_uvs = {i for i, (u, v) in enumerate(zip(us, vs)) if _outside(u, v)}
        # Report faces, not UV ids: a face is the same object in every UV
        # set, while ".map[i]" means "UV i of the CURRENT set" in Maya.
        counts, ids = fn.getAssignedUVs(uv_set)
        ids = list(ids)  # MIntArray -> list, so it can be sliced
        bad_faces, pos = [], 0
        for face, n in enumerate(counts):
            if any(uv in bad_uvs for uv in ids[pos:pos + n]):
                bad_faces.append(face)
            pos += n
        faces += component_names(mesh, "f", bad_faces)

    bounds = {}
    if us_all:
        bounds = {"u_min": min(us_all), "u_max": max(us_all), "v_min": min(vs_all), "v_max": max(vs_all)}
    passed = not faces
    if passed:
        message = "all %s UVs inside 0-1" % uv_set
    else:
        message = "%s with UVs outside 0-1 in %s (range u %.3f..%.3f, v %.3f..%.3f)" % (
            plural(len(faces), "face"), uv_set, bounds["u_min"], bounds["u_max"],
            bounds["v_min"], bounds["v_max"])
    if skipped:
        message += "; %s skipped, no %s set (see uv.present)" % (plural(skipped, "mesh", "meshes"), uv_set)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics=dict(bounds, required=True, faces_outside=len(faces), meshes_skipped=skipped),
                      offenders=faces)
