"""budget.materials: every mesh has 1..max_materials materials.

Each material on a mesh becomes a submesh in Unity, and each submesh is a
separate draw call for every copy on screen. 200 crowd units x 2 materials
= 400 draw calls, far over a low-end phone's ~100. It also breaks GPU
instancing. So on mobile this budget matters as much as the triangle count.

Zero materials fails too: the FBX imports with no material and the mesh
renders with Unity's default (pink or grey) material in game. So do faces
left without a material on a partly assigned mesh (backlog #24): they
become their own default-material submesh.

Counted per mesh, as shading engines (Maya's "material assignment" node);
the report lists the material names behind them.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import component_names, material_faces, mesh_fn, plural

ID = "budget.materials"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every mesh has at least one and at most max_materials materials."


def run(ctx) -> CheckResult:
    limit = ctx.asset_class.max_materials
    per_mesh, over, missing, unassigned = {}, [], [], []
    for mesh in ctx.meshes:
        assigned = material_faces(mesh)
        mats = sorted(assigned)
        per_mesh[mesh] = mats
        if not mats:
            missing.append(mesh)
            continue
        if len(mats) > limit:
            over.append(mesh)
        if None not in assigned.values():  # only per-face assignments: look for gaps
            covered = set().union(*assigned.values())
            gaps = [f for f in range(mesh_fn(mesh).numPolygons) if f not in covered]
            unassigned += component_names(mesh, "f", gaps)

    problems = []
    if over:
        worst = max(over, key=lambda m: (len(per_mesh[m]), m))
        problems.append("%s over the %s limit of %d material(s) per mesh (worst: %s with %s)"
                        % (plural(len(over), "mesh", "meshes"), ctx.asset_class.name, limit,
                           worst, ", ".join(per_mesh[worst])))
    if missing:
        problems.append("%s with no material" % plural(len(missing), "mesh", "meshes"))
    if unassigned:
        problems.append("%s with no material (they render with Unity's default material)"
                        % plural(len(unassigned), "face"))

    passed = not problems
    message = "all meshes within %d material(s)" % limit if passed else "; ".join(problems)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"max_materials": limit,
                               "max_found": max((len(v) for v in per_mesh.values()), default=0),
                               "materials_per_mesh": per_mesh,
                               "unassigned_faces": len(unassigned)},
                      offenders=over + missing + unassigned)
