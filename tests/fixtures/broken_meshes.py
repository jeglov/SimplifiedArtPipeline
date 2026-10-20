"""Build the test scenes: one clean asset + one deliberately broken asset per case.

    mayapy tests/fixtures/broken_meshes.py [out_dir]     (default: out/fixtures)

Scenes are generated, not committed: a script is reviewable in a diff, a
binary/ASCII scene blob is not, and it can't silently drift from what the
test expects. Each case is built to break exactly ONE check where possible
(e.g. the ngon case is a closed cylinder, so it doesn't also fail
open_borders). EXPECT maps each file to the asset class it is validated as
and the check(s) it must fail.

After its builder runs, every fixture is conformed (_conform): scaled to
60 cm, stood on the origin (bottom-centre), transforms frozen, pivots at
the origin. So topology/budget/UV fixtures don't trip the transform checks.
Transform fixtures conform themselves first, then break one rule.
Finally, faces still on Maya's default lambert1 get a material named
M_<AssetName>, so fixtures don't trip the material naming rule.

Not collected by pytest (name doesn't start with test_). The Maya test
suite (tests/maya/) imports build_all(), EXPECT and FIX_EXPECT.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# file name (without .ma) -> (asset class, check ids that must FAIL on it).
# Polygon counts: polyCube = 12 tris. polySphere(sx=n, sy=n) = 2n(n-2) + 2n tris:
# 10x10 = 180, 16x16 = 480, 20x20 = 760.
EXPECT = {
    "SM_Clean": ("prop_small", []),
    "SM_Empty": ("prop_small", ["scene.has_meshes"]),           # empty export: ONLY this check fails
    "SM_NonManifoldEdge": ("prop_small", ["topology.non_manifold"]),
    "SM_NonManifoldVertex": ("prop_small", ["topology.non_manifold"]),
    # Stacked faces share every edge (no border) and also stack in UV space (info).
    "SM_Lamina": ("prop_small", ["topology.lamina_faces", "uv.overlap"]),
    "SM_OpenBorder": ("prop_small", ["topology.open_borders"]),
    "SM_Opposite": ("prop_small", ["topology.inverted"]),        # `opposite` set by hand: FBX drops it
    "SM_FrozenMirror": ("prop_small", ["topology.inverted"]),    # scale -1 frozen: Maya sets `opposite`
    "SM_Reversed": ("prop_small", ["topology.inverted"]),        # Normals > Reverse, with locked normals
    # Open mesh: no inside to measure, only the flag is checked. The fix keeps what Maya shows.
    "SM_OpenOpposite": ("env_module", ["topology.inverted", "topology.open_borders"]),
    "SM_OpenReversed": ("prop_small", ["topology.open_borders"]),  # boundary: open + reversed is invisible
    "SK_SkinnedOpposite": ("character", ["topology.inverted"]),  # fix blocked: the mesh is skinned
    # Backlog #43: winding per shell. Two faces of a cube reversed (the total
    # volume stays positive, so topology.inverted passes), and a combined
    # mesh with one whole cube inside out (total volume 0: "flat", unverifiable).
    # Probed: Maya counts an edge between two oppositely wound faces as
    # non-manifold AND as a border, so those two checks fail too. Only winding says
    # which faces and fixes them (Conform); after the fix both pass.
    "SM_FlippedFaces": ("prop_small", ["topology.non_manifold", "topology.open_borders", "topology.winding"]),
    "SM_FlippedShell": ("prop_small", ["topology.winding"]),
    # A hollow box: an inward cube inside an outward one, as UDF remeshing
    # leaves it. The fix must DELETE the inner shell, never reverse it.
    "SM_InteriorShell": ("prop_small", ["topology.winding"]),
    "SM_Ngon": ("prop_small", ["topology.ngons"]),
    "SM_Pentagon": ("prop_small", ["topology.ngons"]),           # boundary: 5 sides is the smallest ngon
    # Spheres: lat-long UVs keep their area toward the poles while the faces
    # shrink, so the pole rings are >2x denser than the mesh (uv.texel_density, a
    # warning). True of every default polySphere, so it stays in the expectations.
    "SM_OverBudget": ("prop_small", ["budget.tris", "uv.texel_density"]),  # 760 tris > 500
    "SM_LodsGood": ("prop_medium", ["uv.texel_density"]),        # LOD0 760, LOD1 180 (max 418)
    "SM_LodTooHeavy": ("prop_medium", ["budget.lods", "uv.texel_density"]),  # LOD0 760, LOD1 480 (max 418)
    "SM_LodGap": ("prop_hero", ["budget.lods", "uv.texel_density"]),  # LOD0 + LOD2, no LOD1
    "SM_TooManyLods": ("prop_small", ["budget.lods"]),           # 2 levels, prop_small allows 1
    "SM_TwoMaterials": ("prop_small", ["budget.materials"]),     # 2 materials, prop_small allows 1
    "SM_NoMaterial": ("prop_small", ["budget.materials"]),
    "SM_PartialMaterial": ("prop_small", ["budget.materials"]),  # 1 of 6 faces has no material
    "SM_NoUVs": ("prop_small", ["uv.present"]),
    "SM_UnmappedFace": ("prop_small", ["uv.present"]),
    "SM_UVSetName": ("prop_small", ["uv.present"]),              # "UVChannel_1" instead of map1
    "SM_UVSetOrder": ("prop_small", ["uv.present"]),             # lightmap set first -> Unity UV0
    "SM_UVOutside": ("prop_small", ["uv.unit_square"]),          # shifted to u 1..2
    "SM_UVOutsideTiling": ("env_module", []),                    # same, but tiling is allowed
    # Planar Z projection: front and back stack (info), the four sides become lines (zero UV area).
    "SM_UVOverlap": ("prop_small", ["uv.overlap", "uv.texel_density"]),
    # Backlog #13 (warnings): one face's UVs shrunk to 10% (100x fewer texels
    # than its neighbours), and one face's UVs collapsed to a point.
    "SM_TexelStretch": ("prop_small", ["uv.texel_density"]),
    "SM_ZeroUVFace": ("prop_small", ["uv.overlap", "uv.texel_density"]),  # the point lands on a neighbour
    "SM_NotFrozen": ("prop_small", ["transform.frozen"]),        # mesh rotated 90 on Y, not frozen
    "SM_GroupNotFrozen": ("prop_medium", ["transform.frozen", "uv.texel_density"]),  # LOD group scaled 1.2
    "SM_GeoOffset": ("prop_small", ["transform.pivot"]),         # centred on origin, not standing on it
    "SM_PivotMoved": ("prop_small", ["transform.pivot"]),        # Maya pivot moved to a corner
    "SM_PickupCentered": ("pickup", []),                         # pickup = center mode: centred is right
    "SM_PickupOnGround": ("pickup", ["transform.pivot"]),        # standing on origin is wrong for pickups
    "SM_TooSmall": ("prop_small", ["transform.size"]),           # 0.6 cm: authored in metres
    # 600 cm: authored in millimetres. 10x the size spreads the texture 10x thinner too.
    "SM_TooBig": ("prop_small", ["transform.size", "uv.texel_density"]),
    "SM_Meters": ("prop_small", ["scene.units"]),                # scene unit m (warning: still passes)
    "SM_BadMeshName": ("prop_small", ["naming.convention"]),     # mesh "pCube1"
    "SM_BadGroupName": ("prop_small", ["naming.convention"]),    # root group "group1"
    "SM_BadMaterialName": ("prop_small", ["naming.convention"]), # material "lambert2"
    "SM_MixedLods": ("prop_medium", ["naming.convention", "uv.texel_density"]),  # _LOD0 + a mesh without suffix
    "SK_WrongPrefix": ("prop_small", ["naming.convention"]),     # SK_ (skinned) prefix on a static prop
    "SM_ExtraRoot": ("prop_small", ["naming.convention"]),       # stray locator at the root
    "SM_FileMismatch": ("prop_small", ["naming.convention"]),    # file SM_FileMismatch, mesh SM_Crate
    "crate_final_v2": ("prop_small", ["naming.convention"]),     # the classic artist file name
    "SM_WithHistory": ("prop_small", ["scene.history"]),         # live polyTriangulate node
    "SK_Skinned": ("character", []),                             # skinCluster history is allowed
    "SK_SkinnedHistory": ("character", ["scene.history"]),       # polyTriangulate UNDER a skinCluster
    # Stacked faces also stack in UV (info). Fix is blocked: the mesh is skinned.
    "SK_SkinnedLamina": ("character", ["topology.lamina_faces", "uv.overlap"]),
    "SM_NegativeScale": ("prop_small", ["transform.frozen"]),    # mirrored with scale -1: freeze is blocked
    # A realistic messy export: six problems, all safely fixable.
    "SM_Messy": ("prop_small", ["naming.convention", "scene.history", "scene.units",
                                "transform.frozen", "transform.pivot", "uv.present"]),
}

# Checks still failing after `--fix`, where that differs from EXPECT.
# Fixtures not listed here must fail exactly the same checks after --fix
# (nothing safely fixable, or the fix is blocked on purpose).
FIX_EXPECT = {
    "SM_WithHistory": [],
    "SK_SkinnedHistory": [],
    "SM_Lamina": ["topology.open_borders"],  # removing the duplicate reveals a single open quad
    "SM_Opposite": [],
    "SM_FrozenMirror": [],
    "SM_Reversed": [],
    "SM_FlippedFaces": [],
    "SM_FlippedShell": [],
    "SM_InteriorShell": [],
    "SM_OpenOpposite": ["topology.open_borders"],
    "SM_NotFrozen": [],
    "SM_GroupNotFrozen": ["uv.texel_density"],
    "SM_GeoOffset": [],
    "SM_PivotMoved": [],
    "SM_PickupOnGround": [],
    "SM_UVSetName": [],
    "SM_UVSetOrder": [],
    "SM_Meters": [],
    "SM_BadMeshName": [],
    "SM_BadGroupName": [],
    "SM_BadMaterialName": [],
    "SM_Messy": [],
}

# Fixtures whose builder handles placement itself (see _conform).
SELF_PLACED = {"SM_NotFrozen", "SM_GroupNotFrozen", "SM_GeoOffset", "SM_PivotMoved",
               "SM_PickupCentered", "SM_PickupOnGround", "SM_TooSmall", "SM_TooBig", "SM_Meters",
               "SM_WithHistory", "SK_Skinned", "SK_SkinnedHistory", "SK_SkinnedLamina",
               "SM_NegativeScale", "SM_Messy", "SM_Empty", "SM_FrozenMirror", "SK_SkinnedOpposite",
               "SM_InteriorShell"}


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


def _conform(size=60.0):
    """Make the scene a well-placed asset: `size` cm, bbox bottom-centre on
    the origin, transforms frozen, every pivot at the origin, no history."""
    from maya import cmds
    roots = [r for r in cmds.ls(assemblies=True, long=True)
             if cmds.listRelatives(r, allDescendents=True, type="mesh")]
    box = cmds.exactWorldBoundingBox(roots)
    largest = max(box[3] - box[0], box[4] - box[1], box[5] - box[2])
    factor = size / largest
    cmds.scale(factor, factor, factor, roots, pivot=(0, 0, 0), relative=True)
    box = cmds.exactWorldBoundingBox(roots)
    cmds.move(-(box[0] + box[3]) / 2, -box[1], -(box[2] + box[5]) / 2, roots, relative=True)
    cmds.makeIdentity(roots, apply=True, translate=True, rotate=True, scale=True, normal=0)
    # Freezing leaves pivots where they were in world space: reset them.
    for node in roots + (cmds.listRelatives(roots, allDescendents=True, type="transform", fullPath=True) or []):
        cmds.xform(node, pivots=(0, 0, 0), worldSpace=True)
    cmds.delete(roots, constructionHistory=True)
    return roots


def build_empty():
    pass  # a new, empty scene: what a failed export or generation step leaves


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


def _set_opposite(mesh):
    from maya import cmds
    cmds.setAttr(cmds.listRelatives(mesh, shapes=True, fullPath=True)[0] + ".opposite", True)


def _reverse(mesh):
    from maya import cmds
    cmds.polyNormal(mesh, normalMode=0, userNormalMode=1, ch=False)


def _plane(name):
    from maya import cmds
    return _merge(name, [cmds.polyPlane(sx=1, sy=1, ch=False)[0]])


def build_opposite():
    _set_opposite(_merge("SM_Opposite", [_cube()]))


def build_frozen_mirror():
    from maya import cmds
    mesh = _merge("SM_FrozenMirror", [_cube()])
    _conform()
    cmds.scale(-1, 1, 1, mesh)  # symmetric cube: bbox unchanged
    cmds.makeIdentity(mesh, apply=True, scale=True, normal=0)  # Maya sets `opposite` here
    cmds.xform(mesh, pivots=(0, 0, 0), worldSpace=True)


def build_reversed():
    from maya import cmds
    mesh = _merge("SM_Reversed", [_cube()])
    cmds.polyNormalPerVertex(mesh + ".vtx[*]", freezeNormal=True)  # locked, like any FBX input
    cmds.delete(mesh, constructionHistory=True)
    _reverse(mesh)


def build_open_opposite():
    _set_opposite(_plane("SM_OpenOpposite"))


def build_open_reversed():
    _reverse(_plane("SM_OpenReversed"))


def build_skinned_opposite():
    mesh = _merge("SK_SkinnedOpposite", [_cube()])
    _conform()
    _set_opposite(mesh)
    _skin(mesh)


def build_flipped_faces():
    from maya import cmds
    mesh = _merge("SM_FlippedFaces", [_cube()])
    cmds.polyNormal(mesh + ".f[0]", mesh + ".f[3]", normalMode=0, userNormalMode=1, ch=False)


def build_flipped_shell():
    from maya import cmds
    a, b = _cube(-0.75, uv_tile=(0, 0)), _cube(0.75, uv_tile=(1, 0))
    _reverse(b)
    combined = cmds.polyUnite(a, b, ch=False, name="SM_FlippedShell")[0]
    cmds.delete(combined, constructionHistory=True)


def build_interior_shell():
    from maya import cmds
    outer = _cube(uv_tile=(0, 0))
    inner = _cube(uv_tile=(1, 0))
    cmds.scale(0.8, 0.8, 0.8, inner)
    cmds.makeIdentity(inner, apply=True, scale=True)
    _reverse(inner)
    combined = cmds.polyUnite(outer, inner, ch=False, name="SM_InteriorShell")[0]
    cmds.delete(combined, constructionHistory=True)
    # 30 cm: quarter-size UV tiles on a 60 cm box would also miss prop_small's
    # texel density floor; this fixture is about winding only.
    _conform(size=30.0)


def build_texel_stretch():
    from maya import cmds
    mesh = _merge("SM_TexelStretch", [_cube()])
    cmds.polyEditUV(cmds.polyListComponentConversion(mesh + ".f[1]", toUV=True),
                    pivotU=0.5, pivotV=0.5, scaleU=0.1, scaleV=0.1)
    cmds.delete(mesh, constructionHistory=True)


def build_zero_uv_face():
    from maya import cmds
    mesh = _merge("SM_ZeroUVFace", [_cube()])
    # Unshare the face's UVs first, so collapsing them doesn't drag its neighbours along.
    cmds.polyMapCut(cmds.polyListComponentConversion(mesh + ".f[1]", toEdge=True), ch=False)
    cmds.polyEditUV(cmds.polyListComponentConversion(mesh + ".f[1]", toUV=True),
                    pivotU=0.5, pivotV=0.5, scaleU=0.0, scaleV=0.0)
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


def build_partial_material():
    from maya import cmds
    mesh = _merge("SM_PartialMaterial", [_cube()])
    # Removing one face from a whole-object assignment does nothing (Maya
    # rewrites it as f[0:5]). So: clear the default, then assign faces 0-4.
    cmds.sets(mesh, edit=True, remove="initialShadingGroup")
    cmds.sets(mesh + ".f[0:4]", edit=True, forceElement=_material("M_PartialMaterial"))


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


def build_not_frozen():
    from maya import cmds
    mesh = _merge("SM_NotFrozen", [_cube()])
    _conform()
    cmds.rotate(0, 90, 0, mesh)  # around the origin pivot: bbox unchanged


def build_group_not_frozen():
    from maya import cmds
    _lod_asset("SM_GroupNotFrozen", {0: 20, 1: 10})
    _conform()
    cmds.scale(1.2, 1.2, 1.2, "SM_GroupNotFrozen")  # 72 -> 86 cm, bottom stays on the origin


def build_geo_offset():
    from maya import cmds
    mesh = _merge("SM_GeoOffset", [_cube()])
    _conform()
    cmds.move(0, -30, 0, mesh, relative=True)  # centred on the origin instead of standing on it
    cmds.makeIdentity(mesh, apply=True, translate=True)
    cmds.xform(mesh, pivots=(0, 0, 0), worldSpace=True)


def build_pivot_moved():
    from maya import cmds
    mesh = _merge("SM_PivotMoved", [_cube()])
    _conform()
    cmds.xform(mesh, pivots=(30, 0, 30), worldSpace=True)


def build_pickup_centered():
    from maya import cmds
    # 40 cm, like a generated pickup: a 60 cm cube at pickup's 128 px is below
    # its texel density floor (62 < 64 px/m), and this fixture is about pivots.
    mesh = _merge("SM_PickupCentered", [_cube()])
    _conform(size=40.0)
    cmds.move(0, -20, 0, mesh, relative=True)
    cmds.makeIdentity(mesh, apply=True, translate=True)
    cmds.xform(mesh, pivots=(0, 0, 0), worldSpace=True)


def build_pickup_on_ground():
    _merge("SM_PickupOnGround", [_cube()])
    _conform(size=40.0)  # see build_pickup_centered


def build_too_small():
    _merge("SM_TooSmall", [_cube()])
    _conform(size=0.6)


def build_too_big():
    _merge("SM_TooBig", [_cube()])
    _conform(size=600.0)


def build_meters():
    from maya import cmds
    _merge("SM_Meters", [_cube()])
    _conform()
    cmds.currentUnit(linear="m")  # stored in the .ma, restored when the file opens


def build_bad_mesh_name():
    from maya import cmds
    cmds.polyCube(name="pCube1", ch=False)


def build_bad_group_name():
    from maya import cmds
    cmds.group(_merge("SM_BadGroupName", [_cube()]), name="group1")


def build_bad_material_name():
    from maya import cmds
    mesh = _merge("SM_BadMaterialName", [_cube()])
    cmds.sets(mesh, edit=True, forceElement=_material("lambert2"))


def build_mixed_lods():
    # Group first, rename the child after: if a root mesh were already called
    # SM_MixedLods, Maya would silently name the new group "SM_MixedLods1".
    from maya import cmds
    group = cmds.group([_sphere(20, "SM_MixedLods_LOD0"), _sphere(10, "tmp")], name="SM_MixedLods")
    cmds.rename(group + "|tmp", "SM_MixedLods")


def build_wrong_prefix():
    _merge("SK_WrongPrefix", [_cube()])


def build_extra_root():
    from maya import cmds
    _merge("SM_ExtraRoot", [_cube()])
    cmds.spaceLocator(name="locator1")


def build_file_mismatch():
    _merge("SM_Crate", [_cube()])


def build_bad_file_name():
    _merge("SM_Crate", [_cube()])


def build_with_history():
    from maya import cmds
    mesh = _merge("SM_WithHistory", [_cube()])
    _conform()
    cmds.polyTriangulate(mesh, constructionHistory=True)  # tris count unchanged: 12


def _skin(mesh):
    """Two-joint skeleton (its own root, like a real rig) bound to `mesh`."""
    from maya import cmds
    cmds.select(clear=True)  # otherwise the joint is parented under the selection
    root = cmds.joint(name="root_JNT", position=(0, 0, 0))
    cmds.joint(name="top_JNT", position=(0, 60, 0))
    cmds.skinCluster(root, mesh, toSelectedBones=False)


def build_skinned():
    mesh = _merge("SK_Skinned", [_cube()])
    _conform()
    _skin(mesh)


def build_skinned_history():
    from maya import cmds
    mesh = _merge("SK_SkinnedHistory", [_cube()])
    _conform()
    cmds.polyTriangulate(mesh, constructionHistory=True)  # modelling node upstream of the skin
    _skin(mesh)


def build_skinned_lamina():
    from maya import cmds
    q = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    mesh = _merge("SK_SkinnedLamina", [cmds.polyCreateFacet(p=q, ch=False)[0],
                                       cmds.polyCreateFacet(p=list(reversed(q)), ch=False)[0]])
    _conform()
    _skin(mesh)


def build_negative_scale():
    from maya import cmds
    mesh = _merge("SM_NegativeScale", [_cube()])
    _conform()
    cmds.scale(-1, 1, 1, mesh)  # "mirrored" the lazy way: symmetric cube, bbox unchanged


def build_messy():
    """What a rushed or tool-generated export looks like: default mesh and
    material names, a foreign UV set name, live history, unfrozen transform,
    off-centre geometry and a metre scene."""
    from maya import cmds
    mesh = _merge("pCube1", [_cube()])
    _conform()
    cmds.sets(mesh, edit=True, forceElement=_material("lambert2"))
    cmds.polyUVSet(mesh, rename=True, uvSet="map1", newUVSet="UVChannel_1")
    cmds.rotate(0, 45, 0, mesh)
    cmds.move(0, 20, 0, mesh, relative=True)
    cmds.polyTriangulate(mesh, constructionHistory=True)
    cmds.currentUnit(linear="m")


def _default_material(name):
    """Give faces still on lambert1 a material that follows the convention:
    "SM_LodsGood" -> "M_LodsGood", "crate_final_v2" -> "M_FinalV2"."""
    from maya import cmds
    members = cmds.sets("initialShadingGroup", query=True) or []
    if members:
        base = "".join(p[:1].upper() + p[1:] for p in name.split("_")[1:])
        cmds.sets(members, edit=True, forceElement=_material("M_" + base))


def build_pentagon():
    # Boundary case: a 5-sided prism. An octagon alone wouldn't catch an
    # off-by-one threshold (found by mutation testing: "> 5" still passed).
    from maya import cmds
    mesh = _merge("SM_Pentagon", [cmds.polyCylinder(subdivisionsAxis=5, subdivisionsCaps=0, ch=False)[0]])
    cmds.polyEditUV(mesh + ".map[*]", pivotU=0.5, pivotV=0.5, scaleU=0.99, scaleV=0.99)
    cmds.delete(mesh, constructionHistory=True)


BUILDERS = {
    "SM_Clean": build_clean,
    "SM_Empty": build_empty,
    "SM_NonManifoldEdge": build_nonmanifold_edge,
    "SM_NonManifoldVertex": build_nonmanifold_vertex,
    "SM_Lamina": build_lamina,
    "SM_OpenBorder": build_open_border,
    "SM_Opposite": build_opposite,
    "SM_FrozenMirror": build_frozen_mirror,
    "SM_Reversed": build_reversed,
    "SM_OpenOpposite": build_open_opposite,
    "SM_OpenReversed": build_open_reversed,
    "SK_SkinnedOpposite": build_skinned_opposite,
    "SM_FlippedFaces": build_flipped_faces,
    "SM_FlippedShell": build_flipped_shell,
    "SM_InteriorShell": build_interior_shell,
    "SM_TexelStretch": build_texel_stretch,
    "SM_ZeroUVFace": build_zero_uv_face,
    "SM_Ngon": build_ngon,
    "SM_Pentagon": build_pentagon,
    "SM_OverBudget": build_over_budget,
    "SM_LodsGood": build_lods_good,
    "SM_LodTooHeavy": build_lod_too_heavy,
    "SM_LodGap": build_lod_gap,
    "SM_TooManyLods": build_too_many_lods,
    "SM_TwoMaterials": build_two_materials,
    "SM_NoMaterial": build_no_material,
    "SM_PartialMaterial": build_partial_material,
    "SM_NoUVs": build_no_uvs,
    "SM_UnmappedFace": build_unmapped_face,
    "SM_UVSetName": build_uv_set_name,
    "SM_UVSetOrder": build_uv_set_order,
    "SM_UVOutside": build_uv_outside,
    "SM_UVOutsideTiling": build_uv_outside_tiling,
    "SM_UVOverlap": build_uv_overlap,
    "SM_NotFrozen": build_not_frozen,
    "SM_GroupNotFrozen": build_group_not_frozen,
    "SM_GeoOffset": build_geo_offset,
    "SM_PivotMoved": build_pivot_moved,
    "SM_PickupCentered": build_pickup_centered,
    "SM_PickupOnGround": build_pickup_on_ground,
    "SM_TooSmall": build_too_small,
    "SM_TooBig": build_too_big,
    "SM_Meters": build_meters,
    "SM_BadMeshName": build_bad_mesh_name,
    "SM_BadGroupName": build_bad_group_name,
    "SM_BadMaterialName": build_bad_material_name,
    "SM_MixedLods": build_mixed_lods,
    "SK_WrongPrefix": build_wrong_prefix,
    "SM_ExtraRoot": build_extra_root,
    "SM_FileMismatch": build_file_mismatch,
    "crate_final_v2": build_bad_file_name,
    "SM_WithHistory": build_with_history,
    "SK_Skinned": build_skinned,
    "SK_SkinnedHistory": build_skinned_history,
    "SK_SkinnedLamina": build_skinned_lamina,
    "SM_NegativeScale": build_negative_scale,
    "SM_Messy": build_messy,
}
assert set(BUILDERS) == set(EXPECT), "every fixture needs an expectation"
assert set(FIX_EXPECT) <= set(EXPECT), "FIX_EXPECT names an unknown fixture"


def build_all(out_dir):
    """Save every fixture as <out_dir>/<name>.ma. Maya must already be running."""
    from maya import cmds
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name in sorted(BUILDERS):
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear="cm")  # a previous fixture may have changed it
        BUILDERS[name]()
        if name not in SELF_PLACED:
            _conform()
        _default_material(name)
        path = os.path.abspath(os.path.join(out_dir, name + ".ma"))
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii")
        # Same bytes on every build, so exported FBX reports that link back
        # to these scenes by sha256 can be committed as golden files.
        from validate.maya.session import canonicalize_ma
        canonicalize_ma(Path(path))
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
