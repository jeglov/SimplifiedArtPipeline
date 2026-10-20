"""Read binary glTF 2.0 (.glb) without dependencies: what image-to-3D tools output.

GLB = 12-byte header, a JSON chunk, a BIN chunk. glTF fixes the units and
axes by specification: metres, +Y up, +Z forward (front), right-handed. So a
GLB, unlike an FBX (backlog #4), needs no header guessing; only scale is
arbitrary for generated meshes (image-to-3D has no notion of size).

Reads the triangle primitives of every mesh with their node transforms
applied, UV0, and the embedded images. Enough for checks and for converting
into Maya; not a general glTF loader (no skins, morph targets, sparse
accessors, or external .bin files). Pure Python 3.9.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

_COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
TRIANGLES = 4

Vec3 = Tuple[float, float, float]


class GlbError(ValueError):
    """Not a GLB this reader understands. Always a tool error, never a verdict."""


@dataclass
class Primitive:
    mesh: str
    positions: List[Vec3]
    triangles: List[Tuple[int, int, int]]
    uvs: Optional[List[Tuple[float, float]]] = None
    normals: Optional[List[Vec3]] = None
    material: Optional[int] = None


@dataclass
class Glb:
    json: Dict[str, Any]
    primitives: List[Primitive] = field(default_factory=list)
    images: List[Tuple[str, bytes]] = field(default_factory=list)  # (mime type, bytes)

    @property
    def triangle_count(self) -> int:
        return sum(len(p.triangles) for p in self.primitives)

    @property
    def vertex_count(self) -> int:
        """Vertices as stored: glTF already splits them at UV and normal seams,
        so this is close to what a GPU (and Unity) gets."""
        return sum(len(p.positions) for p in self.primitives)

    def bounds(self) -> Tuple[Vec3, Vec3]:
        pts = [v for p in self.primitives for v in p.positions]
        if not pts:
            raise GlbError("the GLB has no geometry")
        return (tuple(min(v[i] for v in pts) for i in range(3)),
                tuple(max(v[i] for v in pts) for i in range(3)))

    def material_names(self) -> List[str]:
        return [m.get("name", "material_%d" % i) for i, m in enumerate(self.json.get("materials") or [])]


def _mat_mul(a: Sequence[float], b: Sequence[float]) -> List[float]:
    """4x4 column-major (glTF layout) a @ b."""
    return [sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4)) for c in range(4) for r in range(4)]


def _node_matrix(node: Dict[str, Any]) -> List[float]:
    if "matrix" in node:
        return list(node["matrix"])
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    r = [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]
    return [r[0] * sx, r[1] * sx, r[2] * sx, 0.0, r[3] * sy, r[4] * sy, r[5] * sy, 0.0,
            r[6] * sz, r[7] * sz, r[8] * sz, 0.0, tx, ty, tz, 1.0]


def _apply(m: Sequence[float], v: Vec3, w: float = 1.0) -> Vec3:
    return (m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12] * w,
            m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13] * w,
            m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14] * w)


def read(path: Union[str, Path]) -> Glb:
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise GlbError("not a GLB (no glTF magic)")
    if len(data) < 20:
        raise GlbError("truncated GLB (%d bytes)" % len(data))
    version, length = struct.unpack("<II", data[4:12])
    if version != 2:
        raise GlbError("glTF version %d, only 2 is supported" % version)
    if length != len(data):
        raise GlbError("GLB header says %d bytes, file has %d" % (length, len(data)))
    chunks = {}
    pos = 12
    while pos + 8 <= len(data):
        n, kind = struct.unpack("<I4s", data[pos:pos + 8])
        chunks.setdefault(kind, data[pos + 8:pos + 8 + n])
        pos += 8 + n
    if b"JSON" not in chunks:
        raise GlbError("GLB has no JSON chunk")
    try:
        doc = json.loads(chunks[b"JSON"].decode("utf-8"))
    except ValueError as exc:
        raise GlbError("GLB JSON chunk is invalid: %s" % exc)
    binary = chunks.get(b"BIN\x00", b"")
    for buf in doc.get("buffers") or []:
        if "uri" in buf:
            raise GlbError("external buffer %r: only self-contained GLB is supported" % buf["uri"])

    def view_bytes(i: int) -> bytes:
        v = doc["bufferViews"][i]
        start = v.get("byteOffset", 0)
        return binary[start:start + v["byteLength"]]

    def accessor(i: int) -> List[Tuple]:
        a = doc["accessors"][i]
        if "sparse" in a or "bufferView" not in a:
            raise GlbError("accessor %d is sparse or has no buffer view" % i)
        fmt, size = _COMPONENT[a["componentType"]]
        n = _COUNT[a["type"]]
        v = doc["bufferViews"][a["bufferView"]]
        stride = v.get("byteStride") or size * n
        base = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        out = []
        for k in range(a["count"]):
            vals = struct.unpack_from("<%d%s" % (n, fmt), binary, base + k * stride)
            if a.get("normalized"):
                vals = tuple(x / float((1 << (8 * size - (1 if fmt.islower() else 0))) - 1) for x in vals)
            out.append(vals)
        return out

    glb = Glb(doc)
    for img in doc.get("images") or []:
        if "bufferView" in img:
            glb.images.append((img.get("mimeType", ""), view_bytes(img["bufferView"])))

    scene = (doc.get("scenes") or [{}])[doc.get("scene", 0)] if doc.get("scenes") else {"nodes": []}
    nodes = doc.get("nodes") or []
    roots = scene.get("nodes") or ([0] if nodes else [])
    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    stack = [(i, identity) for i in reversed(roots)]
    while stack:
        i, parent = stack.pop()
        node = nodes[i]
        m = _mat_mul(parent, _node_matrix(node))
        if "mesh" in node:
            mesh = doc["meshes"][node["mesh"]]
            name = mesh.get("name") or node.get("name") or "mesh_%d" % node["mesh"]
            for prim in mesh["primitives"]:
                if prim.get("mode", TRIANGLES) != TRIANGLES:
                    raise GlbError("mesh %s: primitive mode %s, only triangles are supported" % (name, prim.get("mode")))
                attrs = prim["attributes"]
                pos_ = [_apply(m, p) for p in accessor(attrs["POSITION"])]
                if "indices" in prim:
                    flat = [x[0] for x in accessor(prim["indices"])]
                else:
                    flat = list(range(len(pos_)))
                if len(flat) % 3:
                    raise GlbError("mesh %s: index count %d is not a multiple of 3" % (name, len(flat)))
                tris = [tuple(flat[k:k + 3]) for k in range(0, len(flat), 3)]
                if any(x >= len(pos_) for t in tris for x in t):
                    raise GlbError("mesh %s: an index points past the vertex list" % name)
                uvs = [tuple(x) for x in accessor(attrs["TEXCOORD_0"])] if "TEXCOORD_0" in attrs else None
                normals = None
                if "NORMAL" in attrs:
                    normals = []
                    for nv in accessor(attrs["NORMAL"]):
                        t = _apply(m, nv, 0.0)
                        length = math.sqrt(sum(c * c for c in t)) or 1.0
                        normals.append(tuple(c / length for c in t))
                glb.primitives.append(Primitive(name, pos_, tris, uvs, normals, prim.get("material")))
        for c in reversed(node.get("children") or []):
            stack.append((c, m))
    return glb


def welded(prim_positions: Sequence[Vec3], digits: int = 6) -> List[int]:
    """Index of each vertex after welding identical positions (glTF splits at seams)."""
    seen = {}
    out = []
    for p in prim_positions:
        key = (round(p[0], digits), round(p[1], digits), round(p[2], digits))
        out.append(seen.setdefault(key, len(seen)))
    return out


def edge_use(glb: Glb) -> Dict[Tuple[int, int], int]:
    """How many triangles use each (welded) edge, over all primitives: 1 = open, >2 = non-manifold."""
    counts = {}
    positions = [v for p in glb.primitives for v in p.positions]
    weld = welded(positions)
    offset = 0
    for p in glb.primitives:
        for a, b, c in p.triangles:
            ids = (weld[a + offset], weld[b + offset], weld[c + offset])
            for u, v in ((ids[0], ids[1]), (ids[1], ids[2]), (ids[2], ids[0])):
                key = (u, v) if u < v else (v, u)
                counts[key] = counts.get(key, 0) + 1
        offset += len(p.positions)
    return counts


def _shrunk(tri: Sequence[Tuple[float, float]], eps: float) -> List[Tuple[float, float]]:
    cx = sum(p[0] for p in tri) / 3.0
    cy = sum(p[1] for p in tri) / 3.0
    return [(p[0] + (cx - p[0]) * eps, p[1] + (cy - p[1]) * eps) for p in tri]


def _separated(a: Sequence[Tuple[float, float]], b: Sequence[Tuple[float, float]]) -> bool:
    """Separating axis test for two 2D triangles."""
    for tri in (a, b):
        for k in range(3):
            x1, y1 = tri[k]
            x2, y2 = tri[(k + 1) % 3]
            nx, ny = y2 - y1, x1 - x2
            pa = [nx * p[0] + ny * p[1] for p in a]
            pb = [nx * p[0] + ny * p[1] for p in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return True
    return False


def uv_overlap(glb: Glb, eps: float = 1e-3) -> Tuple[List[Tuple[int, int]], float]:
    """(overlapping triangle pairs, UV area used as a share of 0-1), UV0, all primitives.

    Triangles are shrunk by `eps` toward their centre first, so neighbours
    sharing an edge or a corner don't count. Exact, no rasterising: a baked
    unique texture must have no overlap at any resolution.
    """
    tris = []
    for p in glb.primitives:
        if not p.uvs:
            continue
        for t in p.triangles:
            tris.append([p.uvs[k] for k in t])
    area = 0.0
    boxes = []
    for t in tris:
        (x1, y1), (x2, y2), (x3, y3) = t
        area += abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2.0
        boxes.append((min(x1, x2, x3), min(y1, y2, y3), max(x1, x2, x3), max(y1, y2, y3)))
    order = sorted(range(len(tris)), key=lambda i: boxes[i][0])
    shrunk = [_shrunk(t, eps) for t in tris]
    pairs = []
    for n, i in enumerate(order):
        for j in order[n + 1:]:
            if boxes[j][0] >= boxes[i][2]:
                break  # sorted by min x: nothing further right can touch i
            if boxes[j][1] >= boxes[i][3] or boxes[i][1] >= boxes[j][3]:
                continue
            if not _separated(shrunk[i], shrunk[j]):
                pairs.append((min(i, j), max(i, j)))
    return sorted(pairs), area


def _clip(subject: List[Tuple[float, float]], clip_tri: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman: `subject` polygon clipped by the triangle `clip_tri`."""
    sign = 1.0 if _area2(clip_tri) > 0 else -1.0
    out = list(subject)
    for k in range(3):
        a, b = clip_tri[k], clip_tri[(k + 1) % 3]
        if not out:
            break
        src, out = out, []

        def inside(p):
            return sign * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) >= 0

        for i, p in enumerate(src):
            q = src[(i + 1) % len(src)]
            if inside(p):
                out.append(p)
            if inside(p) != inside(q):
                dx, dy = q[0] - p[0], q[1] - p[1]
                ex, ey = b[0] - a[0], b[1] - a[1]
                den = dx * ey - dy * ex
                if den:
                    t = ((a[0] - p[0]) * ey - (a[1] - p[1]) * ex) / den
                    out.append((p[0] + t * dx, p[1] + t * dy))
    return out


def _area2(poly: Sequence[Tuple[float, float]]) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly)))


def overlap_area(glb: Glb, pairs: Sequence[Tuple[int, int]]) -> float:
    """Total UV area (share of 0-1) shared by the overlapping triangle pairs from uv_overlap()."""
    tris = [[p.uvs[k] for k in t] for p in glb.primitives if p.uvs for t in p.triangles]
    return sum(abs(_area2(_clip(list(tris[i]), tris[j]))) / 2.0 for i, j in pairs)
