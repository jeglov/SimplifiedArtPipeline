"""budget.lods: LOD count is allowed for the class, levels are contiguous,
and each LOD is light enough compared to LOD0.

LOD = Level Of Detail: lighter copies of the mesh that Unity swaps in as
the object gets smaller on screen. Halving the triangles per level is the
standard rule of thumb (mesh_budgets.yaml: max_ratio). A LOD1 that is barely
lighter than LOD0 costs memory and buys no performance.

Levels come from the _LOD<n> name suffix (Unity builds its LODGroup from
the same suffixes). A mesh without a suffix counts as LOD0. Whether the
suffixes are used consistently is the naming check's job (backlog #20).
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import lod_level, tri_count

ID = "budget.lods"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "LOD count is allowed for the class, levels are contiguous and each LOD is light enough."


def run(ctx) -> CheckResult:
    rules = ctx.asset_class.lods
    tris, meshes = {}, {}  # level -> total tris / mesh paths
    for mesh in ctx.meshes:
        level = lod_level(ctx, mesh)
        tris[level] = tris.get(level, 0) + tri_count(mesh)
        meshes.setdefault(level, []).append(mesh)
    levels = sorted(tris)

    problems, offenders, limits = [], [], {}
    if levels != list(range(len(levels))):
        problems.append("LOD levels must run LOD0, LOD1... with no gaps, found %s"
                        % ", ".join("LOD%d" % lv for lv in levels))
    if not rules.min <= len(levels) <= rules.max:
        problems.append("%d LOD level(s), %s allows %d to %d"
                        % (len(levels), ctx.asset_class.name, rules.min, rules.max))

    if 0 in tris:
        for level in levels[1:]:
            if level >= len(rules.max_ratio):
                continue  # LOD4+ is a naming error, reported by the naming check
            limit = rules.max_tris(level, tris[0])
            limits["LOD%d" % level] = limit
            if tris[level] > limit:
                problems.append("LOD%d has %d tris, max %d (%d%% of LOD0 + %d%% tolerance)"
                                % (level, tris[level], limit, rules.max_ratio[level] * 100,
                                   rules.tolerance * 100))
                offenders += meshes[level]

    passed = not problems
    message = ("%d LOD level(s), all within budget" % len(levels)) if passed else "; ".join(problems)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"levels": len(levels),
                               "lods_min": rules.min, "lods_max": rules.max,
                               "tris_per_level": {"LOD%d" % lv: tris[lv] for lv in levels},
                               "max_tris_per_level": limits},
                      offenders=offenders)
