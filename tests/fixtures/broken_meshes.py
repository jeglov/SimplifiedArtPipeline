"""Build the test scenes: one clean asset + one deliberately broken asset per case.

    mayapy tests/fixtures/broken_meshes.py [out_dir]     (default: out/fixtures)

Scenes are generated, not committed: a script is reviewable in a diff, a
binary/ASCII scene blob is not, and it can't silently drift from what the
test expects. Each case is built to break exactly ONE check where possible
(e.g. the ngon case is a closed cylinder, so it doesn't also fail
open_borders). EXPECT maps each file to the check(s) it must fail.

Not collected by pytest (name doesn't start with test_). The Maya test
runner in step 5 imports build_all().
"""
from __future__ import annotations

import os
import sys

# file name (without .ma) -> check ids that must FAIL on it. "clean" fails none.
EXPECT = {
    "SM_Clean": [],
    "SM_NonManifoldEdge": ["topology.non_manifold"],
    "SM_NonManifoldVertex": ["topology.non_manifold"],
    "SM_Lamina": ["topology.lamina_faces"],  # stacked faces share every edge: no border
    "SM_OpenBorder": ["topology.open_borders"],
    "SM_Ngon": ["topology.ngons"],
}


def _merge(name, parts):
    """Combine parts into one mesh, weld coincident vertices, drop history."""
    from maya import cmds
    mesh = cmds.polyUnite(parts, ch=False, name=name)[0] if len(parts) > 1 else cmds.rename(parts[0], name)
    cmds.polyMergeVertex(mesh, distance=0.001, ch=False)
    cmds.delete(mesh, constructionHistory=True)
    return mesh


def _cube(x=0.0, y=0.0, z=0.0):
    from maya import cmds
    cube = cmds.polyCube(ch=False)[0]
    cmds.move(x, y, z, cube)
    cmds.makeIdentity(cube, apply=True, translate=True)
    return cube


def build_clean():
    _merge("SM_Clean", [_cube()])


def build_nonmanifold_edge():
    # Two closed cubes touching along one edge: that edge has 4 faces.
    _merge("SM_NonManifoldEdge", [_cube(), _cube(1, 1, 0)])


def build_nonmanifold_vertex():
    # Two closed cubes touching at one corner: a "bowtie" vertex.
    _merge("SM_NonManifoldVertex", [_cube(), _cube(1, 1, 1)])


def build_lamina():
    # The same quad twice, back to back.
    from maya import cmds
    q = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    _merge("SM_Lamina", [cmds.polyCreateFacet(p=q, ch=False)[0],
                         cmds.polyCreateFacet(p=list(reversed(q)), ch=False)[0]])


def build_open_border():
    from maya import cmds
    mesh = _merge("SM_OpenBorder", [_cube()])
    cmds.delete(mesh + ".f[3]")  # bottom face -> 4 border edges
    cmds.delete(mesh, constructionHistory=True)


def build_ngon():
    # Closed 8-sided cylinder with no cap subdivisions: both caps are octagons.
    from maya import cmds
    _merge("SM_Ngon", [cmds.polyCylinder(subdivisionsAxis=8, subdivisionsCaps=0, ch=False)[0]])


BUILDERS = {
    "SM_Clean": build_clean,
    "SM_NonManifoldEdge": build_nonmanifold_edge,
    "SM_NonManifoldVertex": build_nonmanifold_vertex,
    "SM_Lamina": build_lamina,
    "SM_OpenBorder": build_open_border,
    "SM_Ngon": build_ngon,
}
assert set(BUILDERS) == set(EXPECT), "every fixture needs an expectation"


def build_all(out_dir):
    """Save every fixture as <out_dir>/<name>.ma. Maya must already be running."""
    from maya import cmds
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name in sorted(BUILDERS):
        cmds.file(new=True, force=True)
        BUILDERS[name]()
        path = os.path.abspath(os.path.join(out_dir, name + ".ma"))
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii")
        paths[name] = path
    return paths


if __name__ == "__main__":
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        for name, path in build_all(sys.argv[1] if len(sys.argv) > 1 else "out/fixtures").items():
            print("built", path)
    finally:
        maya.standalone.uninitialize()
