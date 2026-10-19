"""uv.present: required UV sets exist, come first, and map every face.

Unity doesn't read UV set NAMES, it reads their ORDER: the first set in the
FBX becomes UV0 (base color), the second UV1 (lightmap). So a file whose
first set is "lightmap" imports with its base color texture mapped through
the lightmap UVs. Files from other tools often arrive with "UVChannel_1" or
"UVMap" instead of Maya's "map1", which breaks set-name-based tooling.

A face without UVs samples one texel (usually a corner) of the texture and
shows as a flat smear in game.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import component_names, mesh_fn, plural

ID = "uv.present"
SEVERITY = Severity.ERROR
FIXABLE = True  # see validate/maya/fixes/uv_present.py
DESCRIPTION = "Required UV sets exist, come first in the set order, and map every face."


def run(ctx) -> CheckResult:
    required = list(ctx.asset_class.uv.required_sets)
    sets_per_mesh, missing, wrong_order, unmapped = {}, [], [], []
    for mesh in ctx.meshes:
        fn = mesh_fn(mesh)
        names = list(fn.getUVSetNames())
        sets_per_mesh[mesh] = names
        if any(s not in names for s in required):
            missing.append(mesh)
            continue
        if names[:len(required)] != required:
            wrong_order.append(mesh)
        for uv_set in required:
            counts = fn.getAssignedUVs(uv_set)[0]  # UVs per face; 0 = unmapped
            unmapped += component_names(mesh, "f", [i for i, n in enumerate(counts) if n == 0])

    problems = []
    if missing:
        problems.append("%s missing UV set %s (e.g. %s has %s)" % (
            plural(len(missing), "mesh", "meshes"), ", ".join(required),
            missing[0], sets_per_mesh[missing[0]] or "no UV sets"))
    if wrong_order:
        problems.append("%s with UV sets in the wrong order: Unity uses order, not names, so "
                        "the first set must be %s (e.g. %s has %s)" % (
                            plural(len(wrong_order), "mesh", "meshes"), required,
                            wrong_order[0], sets_per_mesh[wrong_order[0]]))
    if unmapped:
        problems.append("%s with no UVs" % plural(len(unmapped), "face"))

    passed = not problems
    message = "all meshes have UV sets %s mapping every face" % required if passed else "; ".join(problems)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"required_sets": required,
                               "meshes_missing_sets": len(missing),
                               "meshes_wrong_order": len(wrong_order),
                               "unmapped_faces": len(unmapped),
                               "uv_sets_per_mesh": sets_per_mesh},
                      offenders=missing + wrong_order + unmapped)
