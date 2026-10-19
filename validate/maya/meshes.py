"""Shared helpers for reading mesh data inside checks.

Two Maya APIs are used, each where it fits best:

  maya.cmds          Commands that return strings. Fine when Maya already has
                     a C++ command doing the work (polyInfo finds non-manifold
                     and lamina components for us).
  maya.api.OpenMaya  (OpenMaya 2.0) Direct access to mesh arrays. Used when we
                     must look at every face/edge ourselves: a Python loop over
                     cmds strings ("SM_Crate.f[12]") is ~10-100x slower on a
                     big mesh, and never touches the selection.

Only import this module after maya.standalone.initialize() (the registry is
imported lazily by the CLI for exactly that reason).
"""
from __future__ import annotations

from typing import Iterable, List

import maya.api.OpenMaya as om
from maya import cmds


def mesh_shape(transform: str) -> str:
    """Full path of the (first, non-intermediate) mesh shape under `transform`."""
    shapes = sorted(cmds.listRelatives(transform, shapes=True, type="mesh",
                                       noIntermediate=True, fullPath=True) or [])
    if not shapes:
        raise ValueError("%s has no mesh shape" % transform)
    return shapes[0]


def mesh_fn(transform: str) -> om.MFnMesh:
    """MFnMesh for the mesh shape under `transform`."""
    sel = om.MSelectionList()
    sel.add(mesh_shape(transform))
    return om.MFnMesh(sel.getDagPath(0))


def short_name(path: str) -> str:
    """"|SM_Crate|SM_Crate_LOD0" -> "SM_Crate_LOD0"."""
    return path.rsplit("|", 1)[-1]


def tri_count(transform: str) -> int:
    """Triangles the GPU will draw: every n-gon counts as n-2 triangles."""
    # getTriangles() -> (triangles per face, vertex ids). Summing the first
    # array is one API call instead of a per-face loop.
    return sum(mesh_fn(transform).getTriangles()[0])


def hierarchy(ctx) -> List[str]:
    """Every transform from the scene roots down to each mesh, sorted.

    "|SM_Crate|SM_Crate_LOD0" -> ["|SM_Crate", "|SM_Crate|SM_Crate_LOD0"].
    Parent groups matter as much as meshes: Unity imports them as
    GameObjects with their own transforms.
    """
    nodes = set()
    for mesh in ctx.meshes:
        parts = mesh.split("|")[1:]  # full path starts with "|"
        for i in range(1, len(parts) + 1):
            nodes.add("|" + "|".join(parts[:i]))
    return sorted(nodes)


def roots(ctx) -> List[str]:
    """Top-level nodes (no parent) that contain the meshes."""
    return sorted({"|" + mesh.split("|")[1] for mesh in ctx.meshes})


def world_bbox(transforms: Iterable[str]) -> om.MBoundingBox:
    """World-space bounding box of the meshes, ALWAYS in centimetres.

    OpenMaya works in internal units (cm) whatever the scene's display unit
    is. cmds.exactWorldBoundingBox answers in the display unit, so the same
    file would give numbers 100x apart in a metre scene.
    """
    box = om.MBoundingBox()
    for transform in transforms:
        sel = om.MSelectionList()
        sel.add(mesh_shape(transform))
        dag = sel.getDagPath(0)
        local = om.MFnDagNode(dag).boundingBox  # in the shape's own space
        local.transformUsing(dag.inclusiveMatrix())
        box.expand(local)
    return box


def transform_fn(node: str) -> om.MFnTransform:
    sel = om.MSelectionList()
    sel.add(node)
    return om.MFnTransform(sel.getDagPath(0))


def xyz(point) -> List[float]:
    """MPoint/MVector -> [x, y, z] floats for JSON metrics."""
    return [point.x, point.y, point.z]


def uv0_set(ctx) -> str:
    """Name of the UV set that becomes UV0 in Unity (the base color UVs), e.g. "map1"."""
    return ctx.asset_class.uv.required_sets[0]


def lod_level(ctx, transform: str) -> int:
    """LOD level from the name suffix. No suffix = a single-LOD asset = level 0."""
    level = ctx.conventions.naming.lod_level(short_name(transform))
    return 0 if level is None else level


def component_names(transform: str, kind: str, indices: Iterable[int]) -> List[str]:
    """(|SM_Crate, "f", [3, 7]) -> ["|SM_Crate.f[3]", "|SM_Crate.f[7]"]."""
    return ["%s.%s[%d]" % (transform, kind, i) for i in indices]


def poly_info(transform: str, flag: str) -> List[str]:
    """Run cmds.polyInfo(<flag>=True) and return one full-path name per component.

    polyInfo returns short names and compressed ranges ("Fin.vtx[0:1]").
    Expanding and renaming makes offenders unique, stable and countable.
    """
    found = cmds.polyInfo(transform, **{flag: True}) or []
    out = []
    for comp in cmds.ls(found, flatten=True) or []:
        # "Fin.vtx[3]" -> ".vtx[3]", re-attached to the full transform path.
        out.append(transform + comp[comp.index("."):])
    return out


def plural(n: int, word: str, many: str = "") -> str:
    """plural(1, "edge") -> "1 edge", plural(3, "edge") -> "3 edges".

    Irregular words pass their plural: plural(2, "vertex", "vertices").
    """
    if n == 1:
        return "1 %s" % word
    return "%d %s" % (n, many or word + "s")
