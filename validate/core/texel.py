"""Texel density (backlog #13): texture pixels per metre of surface.

    density = texture_size x sqrt(UV area / surface area in m^2)

UV area is a share of the 0-1 square (so x texture_size^2 gives pixels);
the square root turns pixels^2 per m^2 into pixels per metre. Measured per
mesh at the class's max texture size (the size it ships at), and per face to
find faces far from the mesh's own density: more than 2x off (one mip level)
reads as a blurry or a crisp patch next to its neighbours.

Probed on Maya 2023 (backlog #13): MItMeshPolygon.getUVArea and
getArea(kWorld); mirrored UVs don't change the area. Pure Python 3.9.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

ZERO_UV = 1e-12    # a face with less UV area than this has no texels at all
OFF_FACTOR = 2.0   # one mip level


@dataclass
class Density:
    px_per_m: float                 # whole mesh
    zero_uv_faces: List[int] = field(default_factory=list)
    off_faces: List[int] = field(default_factory=list)   # > 2x from the mesh's density
    texture_px_needed: int = 0      # smallest POT reaching the target (0 if no target)


def density(faces: Sequence[Tuple[Optional[float], float]], texture_size: int,
            target_px_per_m: float = 0.0) -> Density:
    """faces: (UV area as a share of 0-1, world area in m^2) per face.

    A UV area of None means "no UVs at all" (uv.present reports those): the
    face is left out entirely. 0 means UVs that exist but cover no area.
    """
    faces = [(u, a) if u is not None else None for u, a in faces]
    mapped = [f for f in faces if f is not None]
    uv_total = sum(u for u, _ in mapped)
    area_total = sum(a for _, a in mapped)
    if area_total <= 0:
        return Density(0.0)
    mesh = texture_size * math.sqrt(uv_total / area_total)
    out = Density(mesh)
    for i, face in enumerate(faces):
        if face is None:
            continue
        u, a = face
        if a <= 0:
            continue  # zero-area face: topology.lamina_faces / degenerate, not a texel question
        if u < ZERO_UV:
            out.zero_uv_faces.append(i)
            continue
        d = texture_size * math.sqrt(u / a)
        if mesh > 0 and (d > mesh * OFF_FACTOR or d * OFF_FACTOR < mesh):
            out.off_faces.append(i)
    if target_px_per_m > 0 and uv_total > 0:
        needed = target_px_per_m / math.sqrt(uv_total / area_total)
        out.texture_px_needed = 1 << max(0, math.ceil(math.log2(max(needed, 1.0))))
    return out
