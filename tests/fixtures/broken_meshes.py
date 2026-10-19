"""Build the test scenes: one clean asset + one deliberately broken asset per case.

    mayapy tests/fixtures/broken_meshes.py [out_dir]     (default: out/fixtures)

Scenes are generated, not committed: a script is reviewable in a diff, a
binary/ASCII scene blob is not, and it can't silently drift from what the
test expects. Each case is built to break exactly ONE check where possible
(e.g. the ngon case is a closed cylinder, so it doesn't also fail
open_borders). EXPECT maps each file to the asset class it is validated as
and the check(s) it must fail.

Not collected by pytest (name doesn't start with test_). The Maya test
runner in step 5 imports build_all().
"""
from __future__ import annotations

import os
import sys

# file name (without .ma) -> (asset class, check ids that must FAIL on it).
# Polygon counts: polyCube = 12 tris. polySphere(sx=n, sy=n) = 2n(n-2) + 2n tris:
# 10x10 = 180, 16x16 = 480, 20x20 = 760.
EXPECT = {
    "SM_Clean": ("prop_small", []),
    "SM_NonManifoldEdge": ("prop_small", ["topology.non_manifold"]),
    "SM_NonManifoldVertex": ("prop_small", ["topology.non_manifold"]),
    # Stacked faces share every edge (no border) and also stack in UV space (info).
    "SM_Lamina": ("prop_small", ["topology.lamina_faces", "uv.overlap"]),
    "SM_OpenBorder": ("prop_small", ["topology.open_borders"]),
    "SM_Ngon": ("prop_small", ["topology.ngons"]),
    "SM_OverBudget": ("prop_small", ["budget.tris"]),            # 760 tris > 500
    "SM_LodsGood": ("prop_medium", []),                          # LOD0 760, LOD1 180 (max 418)
    "SM_LodTooHeavy": ("prop_medium", ["budget.lods"]),          # LOD0 760, LOD1 480 (max 418)
    "SM_LodGap": ("prop_hero", ["budget.lods"]),                 # LOD0 + LOD2, no LOD1
    "SM_TooManyLods": ("prop_small", ["budget.lods"]),           # 2 levels, prop_small allows 1
    "SM_TwoMaterials": ("prop_small", ["budget.materials"]),     # 2 materials, prop_small allows 1
    "SM_NoMaterial": ("prop_small", ["budget.materials"]),
    "SM_NoUVs": ("prop_small", ["uv.present"]),
    "SM_UnmappedFace": ("prop_small", ["uv.present"]),
    "SM_UVSetName": ("prop_small", ["uv.present"]),              # "UVChannel_1" instead of map1
    "SM_UVSetOrder": ("prop_small", ["uv.present"]),             # lightmap set first -> Unity UV0
    "SM_UVOutside": ("prop_small", ["uv.unit_square"]),          # shifted to u 1..2
    "SM_UVOutsideTiling": ("env_module", []),                    # same, but tiling is allowed
    "SM_UVOverlap": ("prop_small", ["uv.overlap"]),              # severity info: report still passes
}


def _merge(name, parts):
    """Combine parts into one mesh, weld coincident vertices, drop history."""
    from maya import cmds
    mesh = cmds.polyUnite(parts, ch=False, name=name)[0] if len(parts) > 1 else cmds.rename(parts[0], name)
    cmds.polyMergeVertex(mesh, distance=0.001, ch=False)
    cmds.delete(mesh, constructionHistory=True)
    return mesh


def _cube(x=0.0, y=0.0, z=0.0, uv_tile=None):
    """polyCube moved to (x, y, z). uv_tile=(i, j) shrinks its UVs into one
    quarter of 0-1, so two cubes combined into one mesh don't overlap in UV."""
    from maya import cmds
    cube = cmds.polyCube(ch=False)[0]
    if uv_tile is not None:
        cmds.polyEditUV(cube + ".map[*]", pivotU=0, pivotV=0, scaleU=0.5, scaleV=0.5)
        cmds.polyEditUV(cube + ".map[*]", u=0.5 * uv_tile[0], v=0.5 * uv_tile[1])
    cmds.move(x, y, z, cube)
    cmds.makeIdentity(cube, apply=True, translate=True)
    return cube


def build_clean():
    _merge("SM_Clean", [_cube()])


def build_nonmanifold_edge():
    # Two closed cubes touching along one edge: that edge has 4 faces.
    _merge("SM_NonManifoldEdge", [_cube(uv_tile=(0, 0)), _cube(1, 1, 0, uv_tile=(1, 1))])


def build_nonmanifold_vertex():
    # Two closed cubes touching at one corner: a "bowtie" vertex.
    _merge("SM_NonManifoldVertex", [_cube(uv_tile=(0, 0)), _cube(1, 1, 1, uv_tile=(1, 1))])


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
    mesh = _merge("SM_Ngon", [cmds.polyCylinder(subdivisionsAxis=8, subdivisionsCaps=0, ch=False)[0]])
    # Maya's default cylinder puts two cap UVs at u=1.0019, just outside 0-1.
    # Shrink slightly so this fixture only breaks topology.ngons.
    cmds.polyEditUV(mesh + ".map[*]", pivotU=0.5, pivotV=0.5, scaleU=0.99, scaleV=0.99)
    cmds.delete(mesh, constructionHistory=True)


def _sphere(n, name):
    from maya import cmds
    return cmds.rename(cmds.polySphere(subdivisionsX=n, subdivisionsY=n, ch=False)[0], name)


def _lod_asset(name, levels):
    """Group of LOD meshes, e.g. levels={0: 20, 1: 10} -> spheres with those subdivisions."""
    from maya import cmds
    parts = [_sphere(n, "%s_LOD%d" % (name, level)) for level, n in sorted(levels.items())]
    cmds.group(parts, name=name)


def build_over_budget():
    _sphere(20, "SM_OverBudget")


def build_lods_good():
    _lod_asset("SM_LodsGood", {0: 20, 1: 10})


def build_lod_too_heavy():
    _lod_asset("SM_LodTooHeavy", {0: 20, 1: 16})


def build_lod_gap():
    _lod_asset("SM_LodGap", {0: 20, 2: 10})


def build_too_many_lods():
    _lod_asset("SM_TooManyLods", {0: 10, 1: 4})


def _material(name):
    """lambert + shading engine (Maya's "material assignment" node)."""
    from maya import cmds
    shader = cmds.shadingNode("lambert", asShader=True, name=name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    return sg


def build_two_materials():
    from maya import cmds
    mesh = _merge("SM_TwoMaterials", [_cube()])
    cmds.sets(mesh + ".f[0:2]", edit=True, forceElement=_material("M_TwoMaterials_A"))
    cmds.sets(mesh + ".f[3:5]", edit=True, forceElement=_material("M_TwoMaterials_B"))


def build_no_material():
    from maya import cmds
    mesh = _merge("SM_NoMaterial", [_cube()])
    cmds.sets(mesh, edit=True, remove="initialShadingGroup")


def build_no_uvs():
    from maya import cmds
    mesh = _merge("SM_NoUVs", [_cube()])
    cmds.polyMapDel(mesh + ".f[*]", ch=False)


def build_unmapped_face():
    from maya import cmds
    mesh = _merge("SM_UnmappedFace", [_cube()])
    cmds.polyMapDel(mesh + ".f[0]", ch=False)


def build_uv_set_name():
    from maya import cmds
    mesh = _merge("SM_UVSetName", [_cube()])
    cmds.polyUVSet(mesh, rename=True, uvSet="map1", newUVSet="UVChannel_1")


def build_uv_set_order():
    from maya import cmds
    mesh = _merge("SM_UVSetOrder", [_cube()])
    cmds.polyUVSet(mesh, create=True, uvSet="lightmap")
    cmds.polyCopyUV(mesh, uvSetNameInput="map1", uvSetName="lightmap", ch=False)
    cmds.polyUVSet(mesh, reorder=True, uvSet="lightmap", newUVSet="map1")
    # Leave lightmap as the CURRENT set (artists often do): exercises the
    # switch-and-restore path in uv.overlap.
    cmds.polyUVSet(mesh, currentUVSet=True, uvSet="lightmap")


def _shift_uvs(mesh, u):
    from maya import cmds
    cmds.polyEditUV(mesh + ".map[*]", u=u, v=0.0)
    cmds.delete(mesh, constructionHistory=True)


def build_uv_outside():
    _shift_uvs(_merge("SM_UVOutside", [_cube()]), 1.0)


def build_uv_outside_tiling():
    _shift_uvs(_merge("SM_UVOutsideTiling", [_cube()]), 1.0)


def build_uv_overlap():
    # Planar projection along Z: front and back faces land on the same UVs.
    from maya import cmds
    mesh = _merge("SM_UVOverlap", [_cube()])
    cmds.polyProjection(mesh + ".f[*]", type="Planar", md="z", ch=False)


BUILDERS = {
    "SM_Clean": build_clean,
    "SM_NonManifoldEdge": build_nonmanifold_edge,
    "SM_NonManifoldVertex": build_nonmanifold_vertex,
    "SM_Lamina": build_lamina,
    "SM_OpenBorder": build_open_border,
    "SM_Ngon": build_ngon,
    "SM_OverBudget": build_over_budget,
    "SM_LodsGood": build_lods_good,
    "SM_LodTooHeavy": build_lod_too_heavy,
    "SM_LodGap": build_lod_gap,
    "SM_TooManyLods": build_too_many_lods,
    "SM_TwoMaterials": build_two_materials,
    "SM_NoMaterial": build_no_material,
    "SM_NoUVs": build_no_uvs,
    "SM_UnmappedFace": build_unmapped_face,
    "SM_UVSetName": build_uv_set_name,
    "SM_UVSetOrder": build_uv_set_order,
    "SM_UVOutside": build_uv_outside,
    "SM_UVOutsideTiling": build_uv_outside_tiling,
    "SM_UVOverlap": build_uv_overlap,
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
