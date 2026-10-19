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


def mesh_fn(transform: str) -> om.MFnMesh:
    """MFnMesh for the (first, non-intermediate) mesh shape under `transform`."""
    shapes = sorted(cmds.listRelatives(transform, shapes=True, type="mesh",
                                       noIntermediate=True, fullPath=True) or [])
    if not shapes:
        raise ValueError("%s has no mesh shape" % transform)
    sel = om.MSelectionList()
    sel.add(shapes[0])
    return om.MFnMesh(sel.getDagPath(0))


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
