"""topology.winding: every shell agrees with itself, and no shell of a combined mesh faces inward.

topology.inverted judges a whole mesh by its total signed volume. Two cases
it can't see (backlog #43), both common after a bad mirror or combine and in
image-to-3D output:

  * faces wound against their neighbours inside one shell: on a manifold
    edge the two faces must run in opposite directions. The smaller group
    of each shell is reported (validate.core.winding).
  * a whole shell inside out in a mesh with several shells: consistent with
    itself, but its closed volume is negative. Probed on Maya 2023: two
    cubes combined, one reversed, total volume 0, and Maya's own Conform
    leaves it inward.
  * an interior shell: inward-facing AND enclosed by an outward shell. The
    inner wall of a hollow mesh, which unsigned-distance remeshing leaves in
    image-to-3D output (probed: a generated gem spent 118 of its 270
    triangles on one). Reversing it would hide the problem; it is deleted.

Single-shell inside-out meshes stay topology.inverted's job. Shells that
can't be made consistent (Moebius-like) are reported and can't be fixed.
"""
from __future__ import annotations

from validate.core import winding
from validate.core.result import CheckResult, Severity
from validate.maya.meshes import mesh_fn, plural

ID = "topology.winding"
SEVERITY = Severity.ERROR
FIXABLE = True  # see validate/maya/fixes/topology_winding.py
DESCRIPTION = "Faces agree with their shell's winding; no closed shell faces inward or hides inside another."


def analyse(mesh: str) -> dict:
    """{"faces": [...flipped faces], "inward": [[faces of an inward shell], ...], "non_orientable": n}."""
    fn = mesh_fn(mesh)
    counts, connects = fn.getVertices()
    faces, k = [], 0
    for c in counts:
        faces.append(list(connects[k:k + c]))
        k += c
    points = [(p.x, p.y, p.z) for p in fn.getPoints()]
    shells = winding.shells(faces)
    flipped = [f for s in shells for f in s.flipped]
    interior, inward = winding.classify_inward(points, faces) if len(shells) > 1 else ([], [])
    return {"shells": len(shells), "faces": flipped, "inward": inward, "interior": interior,
            "non_orientable": sum(1 for s in shells if not s.orientable)}


def run(ctx) -> CheckResult:
    found, offenders = {}, []
    for mesh in ctx.meshes:
        a = analyse(mesh)
        if a["faces"] or a["inward"] or a["interior"] or a["non_orientable"]:
            found[mesh] = a
            offenders += ["%s.f[%d]" % (mesh, f) for f in a["faces"]]
            offenders += ["%s.f[%d]" % (mesh, f) for s in a["inward"] + a["interior"] for f in s]
            if a["non_orientable"] and not a["faces"]:
                offenders.append(mesh)
    faces = sum(len(a["faces"]) for a in found.values())
    inward = sum(len(a["inward"]) for a in found.values())
    interior = sum(len(a["interior"]) for a in found.values())
    interior_faces = sum(len(s) for a in found.values() for s in a["interior"])
    bad = sum(a["non_orientable"] for a in found.values())
    parts = []
    if faces:
        parts.append("%s wound against the rest of their shell" % plural(faces, "face", "faces"))
    if inward:
        parts.append("%s facing inward in a combined mesh" % plural(inward, "closed shell", "closed shells"))
    if interior:
        parts.append("%s inside another shell (%s nobody can see)"
                     % (plural(interior, "interior shell", "interior shells"),
                        plural(interior_faces, "face", "faces")))
    if bad:
        parts.append("%s can't be made consistent (non-orientable)" % plural(bad, "shell", "shells"))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=not found,
                      message="; ".join(parts) or "every shell is consistently wound",
                      metrics={"flipped_faces": faces, "inward_shells": inward, "interior_shells": interior,
                               "interior_faces": interior_faces, "non_orientable_shells": bad,
                               "meshes": {m: {"flipped_faces": len(a["faces"]), "inward_shells": len(a["inward"]),
                                              "interior_shells": len(a["interior"]),
                                              "non_orientable": a["non_orientable"]} for m, a in found.items()}},
                      offenders=offenders)
