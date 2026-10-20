"""Winding consistency per shell (backlog #43): which faces run the wrong way round?

On a manifold edge, the two faces that share it must traverse it in opposite
directions (a->b in one, b->a in the other). A face that traverses it the
same way as its neighbour is wound the other way round: its normal points to
the other side of the surface. That is local, so it finds one flipped shell
in a combined mesh, or a few flipped faces after a bad mirror, which a
whole-mesh signed volume (topology.inverted) can miss.

Each connected shell is walked from a seed face, labelling every face "same
as seed" or "flipped". The smaller group is reported: on a shell that is
mostly right, those are the wrong faces. Which side is *outside* is
topology.inverted's question; this only asks whether a shell agrees with
itself. A shell whose labels contradict each other (a Moebius-like strip)
can't be made consistent and is reported as such.

Non-manifold edges (3+ faces) don't propagate labels: they are
topology.non_manifold's job. Pure Python 3.9.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple


@dataclass
class Shell:
    faces: List[int]
    flipped: List[int] = field(default_factory=list)   # the minority side
    orientable: bool = True


def shells(faces: Sequence[Sequence[int]]) -> List[Shell]:
    """faces: vertex indices per face, in winding order. Faces with < 3 vertices are ignored."""
    edges: Dict[Tuple[int, int], List[Tuple[int, bool]]] = {}
    for f, verts in enumerate(faces):
        n = len(verts)
        if n < 3:
            continue
        for k in range(n):
            a, b = verts[k], verts[(k + 1) % n]
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            edges.setdefault(key, []).append((f, a < b))  # (face, traverses low->high)

    neighbours: Dict[int, List[Tuple[int, bool]]] = {}
    for users in edges.values():
        if len(users) != 2:
            continue  # open (1) or non-manifold (3+): no constraint
        (f1, d1), (f2, d2) = users
        if f1 == f2:
            continue
        # Same direction on a shared edge: the faces disagree.
        neighbours.setdefault(f1, []).append((f2, d1 == d2))
        neighbours.setdefault(f2, []).append((f1, d1 == d2))

    label: Dict[int, bool] = {}
    out = []
    for seed in range(len(faces)):
        if seed in label or len(faces[seed]) < 3:
            continue
        label[seed] = False
        shell = Shell([seed])
        stack = [seed]
        while stack:
            f = stack.pop()
            for g, disagree in neighbours.get(f, ()):
                want = label[f] != disagree
                if g not in label:
                    label[g] = want
                    shell.faces.append(g)
                    stack.append(g)
                elif label[g] != want:
                    shell.orientable = False
        flipped = [f for f in shell.faces if label[f]]
        same = [f for f in shell.faces if not label[f]]
        shell.faces.sort()
        # Ties keep the seed's side (lowest face index): stable across runs.
        shell.flipped = sorted(flipped if len(flipped) <= len(same) else same)
        out.append(shell)
    return out


def signed_volume(points: Sequence[Sequence[float]], faces: Sequence[Sequence[int]]) -> float:
    """6x the signed volume enclosed by `faces` (fan-triangulated): < 0 when they face inward.

    Only meaningful for a closed shell; the caller decides that (open_edges()).
    """
    total = 0.0
    for verts in faces:
        a = points[verts[0]]
        for k in range(1, len(verts) - 1):
            b, c = points[verts[k]], points[verts[k + 1]]
            total += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return total


def open_edges(faces: Sequence[Sequence[int]]) -> int:
    """Edges used by exactly one of `faces`: 0 means the faces form a closed surface."""
    count: Dict[Tuple[int, int], int] = {}
    for verts in faces:
        for k in range(len(verts)):
            a, b = verts[k], verts[(k + 1) % len(verts)]
            key = (a, b) if a < b else (b, a)
            count[key] = count.get(key, 0) + 1
    return sum(1 for n in count.values() if n == 1)


def bbox(points: Sequence[Sequence[float]], faces: Sequence[Sequence[int]]):
    ids = {v for f in faces for v in f}
    return (tuple(min(points[v][i] for v in ids) for i in range(3)),
            tuple(max(points[v][i] for v in ids) for i in range(3)))


def classify_inward(points: Sequence[Sequence[float]], faces: Sequence[Sequence[int]]):
    """Split the closed, consistent, inward-facing shells into (interior, inside_out).

    interior: enclosed (bounding box inside) by an outward closed shell. The
      inner wall of a hollow mesh, which unsigned-distance remeshing creates
      (probed: a generated gem, 118 of its 270 triangles on an inner shell).
      Nobody can see it: it should be deleted, never reversed.
    inside_out: not enclosed by anything. A part that really is flipped.

    Each is a list of shells, a shell being a list of face indices.
    """
    found = []
    for s in shells(faces):
        mine = [faces[i] for i in s.faces]
        if not s.orientable or s.flipped or open_edges(mine):
            continue
        vol = signed_volume(points, mine)
        found.append((s.faces, vol, bbox(points, mine)))
    outward = [(f, b) for f, v, b in found if v > 0]
    interior, inside_out = [], []
    for f, v, b in found:
        if v >= 0:
            continue
        lo, hi = b
        enclosed = any(all(olo[i] <= lo[i] and hi[i] <= ohi[i] for i in range(3))
                       for _, (olo, ohi) in outward)
        (interior if enclosed else inside_out).append(f)
    return interior, inside_out
