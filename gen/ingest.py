"""Build a Maya scene from a generated model: the hand-off to the Maya stage.

Runs inside mayapy (gen.cli ingest starts Maya). The scene it saves is an
ordinary asset scene, so the existing path takes it from there unchanged:
`artpipe build SM_<Name>.ma --class C --texture T_<Name>_D.png ...` (Maya
validate --fix --export, then the Unity import gate). Generated assets get no
shortcut.

What it writes into <out>/<Name>/:
  SM_<Name>.ma        one mesh named like the file, frozen, history-free,
                      a plain lambert M_<Name> (no texture paths: see below),
                      its baked normals locked (the normal map was baked
                      against exactly these normals)
  T_<Name>_D.png      base colour, T_<Name>_N.png normal map (tangent space)
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

from gen.core.ingest import MeshArrays


def build_scene(arrays: MeshArrays, name: str, out_dir: Path, textures: Dict[str, Path]) -> Path:
    """`textures`: {"D": base colour png, "N": normal png}. Returns the saved scene."""
    import maya.api.OpenMaya as om
    from maya import cmds

    from validate.maya import session

    cmds.file(new=True, force=True)
    cmds.currentUnit(linear="cm")
    cmds.upAxis(axis="y")
    stem = "SM_" + name

    fn = om.MFnMesh()
    points = om.MPointArray([om.MPoint(*p) for p in arrays.points])
    obj = fn.create(points, arrays.counts, arrays.connects, arrays.us, arrays.vs)
    fn.assignUVs(arrays.counts, arrays.uv_ids)
    if arrays.normals:
        faces, verts = [], []
        for f, count in enumerate(arrays.counts):
            faces.extend([f] * count)
        verts = list(arrays.connects)
        fn.setFaceVertexNormals([om.MVector(*n) for n in arrays.normals], faces, verts)
    transform = om.MFnDagNode(obj).fullPathName()
    transform = cmds.rename(transform, stem)
    shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
    cmds.rename(shape, stem + "Shape")
    cmds.sets(transform, edit=True, forceElement="initialShadingGroup")  # replaced below

    out_dir.mkdir(parents=True, exist_ok=True)
    tex_names = {}
    for suffix, src in sorted(textures.items()):
        dest = out_dir / ("T_%s_%s.png" % (name, suffix))
        shutil.copyfile(str(src), str(dest))
        tex_names[suffix] = dest.name

    # A plain lambert named M_<Name>, like a hand-made asset: Unity remaps the
    # FBX material by name to a shared Assets/Art/Materials/M_<Name>.mat and
    # never reads textures from the FBX. Probed: file texture nodes make the
    # FBX exporter write absolute paths, which the canonical export refuses.
    shader = cmds.shadingNode("lambert", asShader=True, name="M_" + name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="M_%sSG" % name)
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    cmds.sets(transform, edit=True, forceElement=sg)
    cmds.delete(transform, constructionHistory=True)

    scene = out_dir / (stem + ".ma")
    session.save_scene(scene)
    return scene
