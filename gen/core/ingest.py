"""GLB -> the arrays Maya's MFnMesh.create needs, placed and sized for an asset class.

Pure Python 3.9 (gen/ingest.py does the Maya part). Three conversions:

  * Weld. glTF duplicates a vertex wherever its UV or normal changes (a GPU
    layout). Maya wants one vertex per position with per-corner UVs and
    normals; unwelded, every UV seam would be an open border.
  * UV origin. glTF puts (0, 0) at the image's TOP-left, Maya and Unity at
    the bottom-left: v becomes 1 - v, or the baked texture lands upside
    down inside every island.
  * Size and placement. glTF is metres and +Y up, as is Maya's default
    up-axis, so no axis change. Image-to-3D has no idea of real size (every
    output is ~1 unit), so the largest dimension is scaled to the size
    chosen for the class (generation.yaml `ingest.size_cm`) and the mesh is
    placed for the class pivot rule (bbox bottom-centre or centre on the
    origin).

Degenerate triangles (two corners welded together) are dropped and counted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from validate.core.glb import Glb, GlbError

Vec3 = Tuple[float, float, float]
WELD_DIGITS = 6


@dataclass
class MeshArrays:
    points: List[Vec3]                       # cm, placed
    counts: List[int]                        # vertices per face (all 3)
    connects: List[int]                      # vertex ids per face corner
    us: List[float] = field(default_factory=list)
    vs: List[float] = field(default_factory=list)
    uv_ids: List[int] = field(default_factory=list)   # per face corner
    normals: List[Vec3] = field(default_factory=list)  # per face corner
    dropped: int = 0                         # degenerate triangles
    scale: float = 1.0                       # GLB units -> cm


def arrays(glb: Glb, size_cm: float, pivot_mode: str) -> MeshArrays:
    if not glb.primitives:
        raise GlbError("the GLB has no geometry")
    if any(p.uvs is None for p in glb.primitives):
        raise GlbError("every primitive needs UV0 (the baked textures use it)")
    lo, hi = glb.bounds()
    largest = max(h - l for l, h in zip(lo, hi))
    if largest <= 0:
        raise GlbError("the GLB is flat or a point (largest dimension %s)" % largest)
    s = size_cm / largest
    if pivot_mode == "bottom_center":
        anchor = ((lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2)
    else:  # center; "origin" assets have no geometry constraint, centring is the neutral choice
        anchor = tuple((l + h) / 2 for l, h in zip(lo, hi))

    out = MeshArrays([], [], [], scale=s)
    weld = {}
    uv_index = {}
    for prim in glb.primitives:
        ids = []
        for p in prim.positions:
            q = tuple(round((p[i] - anchor[i]) * s, WELD_DIGITS) for i in range(3))
            if q not in weld:
                weld[q] = len(out.points)
                out.points.append(q)
            ids.append(weld[q])
        for tri in prim.triangles:
            corners = [ids[k] for k in tri]
            if len(set(corners)) < 3:
                out.dropped += 1
                continue
            out.counts.append(3)
            out.connects.extend(corners)
            for k in tri:
                u, v = prim.uvs[k]
                key = (round(u, 7), round(1.0 - v, 7))
                if key not in uv_index:
                    uv_index[key] = len(out.us)
                    out.us.append(key[0])
                    out.vs.append(key[1])
                out.uv_ids.append(uv_index[key])
                if prim.normals is not None:
                    out.normals.append(prim.normals[k])
    if out.normals and len(out.normals) != len(out.connects):
        out.normals = []  # mixed primitives with and without normals: let Maya compute them
    if not out.counts:
        raise GlbError("every triangle is degenerate after welding")
    return out


def bounds(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3]:
    return (tuple(min(p[i] for p in points) for i in range(3)),
            tuple(max(p[i] for p in points) for i in range(3)))
