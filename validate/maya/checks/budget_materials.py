"""budget.materials: every mesh has 1..max_materials materials.

Each material on a mesh becomes a submesh in Unity, and each submesh is a
separate draw call for every copy on screen. 200 crowd units x 2 materials
= 400 draw calls, far over a low-end phone's ~100. It also breaks GPU
instancing. So on mobile this budget matters as much as the triangle count.

Zero materials fails too: the FBX imports with no material and the mesh
renders with Unity's default (pink or grey) material in game.

Counted per mesh, as shading engines (Maya's "material assignment" node);
the report lists the material names behind them.
"""
from __future__ import annotations

from maya import cmds

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import mesh_shape, plural

ID = "budget.materials"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every mesh has at least one and at most max_materials materials."


def _materials(transform: str):
    """Sorted material names assigned to the mesh (one per shading engine)."""
    shape = mesh_shape(transform)
    names = []
    for sg in set(cmds.listConnections(shape, type="shadingEngine") or []):
        # A connection alone isn't an assignment: reassigning faces often
        # leaves a stale link to initialShadingGroup with no faces in it.
        # Count the engine only if this mesh (or some of its faces) is a member.
        members = cmds.ls(cmds.sets(sg, query=True) or [], long=True)
        if not any(m.split(".")[0] in (transform, shape) for m in members):
            continue
        shader = cmds.listConnections(sg + ".surfaceShader") or ["<no shader>"]
        names.append(shader[0])
    return sorted(names)


def run(ctx) -> CheckResult:
    limit = ctx.asset_class.max_materials
    per_mesh, over, missing = {}, [], []
    for mesh in ctx.meshes:
        mats = _materials(mesh)
        per_mesh[mesh] = mats
        if not mats:
            missing.append(mesh)
        elif len(mats) > limit:
            over.append(mesh)

    problems = []
    if over:
        worst = max(over, key=lambda m: (len(per_mesh[m]), m))
        problems.append("%s over the %s limit of %d material(s) per mesh (worst: %s with %s)"
                        % (plural(len(over), "mesh", "meshes"), ctx.asset_class.name, limit,
                           worst, ", ".join(per_mesh[worst])))
    if missing:
        problems.append("%s with no material" % plural(len(missing), "mesh", "meshes"))

    passed = not problems
    message = "all meshes within %d material(s)" % limit if passed else "; ".join(problems)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"max_materials": limit,
                               "max_found": max((len(v) for v in per_mesh.values()), default=0),
                               "materials_per_mesh": per_mesh},
                      offenders=over + missing)
