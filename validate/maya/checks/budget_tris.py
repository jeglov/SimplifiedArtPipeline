"""budget.tris: LOD0 triangle count is within the asset class budget.

Only LOD0 is compared to tris_max: it's the version drawn close to the
camera, the worst case for the frame. Lower LODs are checked relative to
LOD0 by budget.lods.

Triangles, not faces: the GPU only draws triangles, a quad costs 2 and an
ngon n-2. Note the real GPU cost is the vertex count AFTER Unity splits
vertices on UV seams and hard edges (backlog #11, measured in Phase 2).
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import lod_level, tri_count

ID = "budget.tris"
SEVERITY = Severity.ERROR
FIXABLE = False  # reducing triangles needs an artist (or a decimation step + review)
DESCRIPTION = "LOD0 triangle count is within the asset class budget."


def run(ctx) -> CheckResult:
    limit = ctx.asset_class.tris_max
    lod0 = [m for m in ctx.meshes if lod_level(ctx, m) == 0]
    lod0_tris = sum(tri_count(m) for m in lod0)
    total_tris = sum(tri_count(m) for m in ctx.meshes)

    passed = lod0_tris <= limit
    percent = 100.0 * lod0_tris / limit
    if passed:
        message = "LOD0 has %d tris, %d%% of the %s budget (%d)" % (
            lod0_tris, percent, ctx.asset_class.name, limit)
    else:
        message = "LOD0 has %d tris, over the %s budget of %d by %d (%d%%)" % (
            lod0_tris, ctx.asset_class.name, limit, lod0_tris - limit, percent - 100)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"lod0_tris": lod0_tris, "tris_max": limit,
                               "percent_of_budget": round(percent, 1),
                               "total_tris_all_lods": total_tris},
                      offenders=[] if passed else lod0)
