"""validate.core.glb (GLB reader) and gen.core.model (checks on generated meshes).

Pure Python, <1 s. Recorded TRELLIS.2 outputs (tests/fixtures/gen/):
gem_model.glb (passes, with a hidden inner shell as a warning), coin_model.glb (270 tris, closed, but its
unwrap overlaps 42 triangles) and coin_ring_model.glb (the first attempt,
one-step decimation: both faces lost).
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from gen.core import model
from validate.core import conventions as conv
from validate.core.glb import GlbError, edge_use, read
from validate.core.image import encode_png

GEN = Path(__file__).resolve().parent / "fixtures" / "gen"
C = conv.load()
PICKUP = C.asset_class("pickup")

CUBE_V = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
CUBE_T = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
          (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3)]


def glb_bytes(vertices=CUBE_V, triangles=CUBE_T, uvs=True, node=None, textures=True, mode=4,
              index_type=5125, indices=True, version=2, normal_map=True):
    binary = b"".join(struct.pack("<3f", *v) for v in vertices)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": len(vertices), "type": "VEC3"}]
    attrs = {"POSITION": 0}
    prim = {"attributes": attrs, "mode": mode}
    if uvs:
        uv = b"".join(struct.pack("<2f", *(uvs[i] if isinstance(uvs, list) else (0.5, 0.5))) for i in range(len(vertices)))
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(uv)})
        accessors.append({"bufferView": 1, "componentType": 5126, "count": len(vertices), "type": "VEC2"})
        attrs["TEXCOORD_0"] = 1
        binary += uv
    if indices:
        fmt = {5125: "I", 5123: "H"}[index_type]
        idx = b"".join(struct.pack("<3" + fmt, *t) for t in triangles)
        idx += b"\x00" * (-len(idx) % 4)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(triangles) * 3 * struct.calcsize(fmt)})
        accessors.append({"bufferView": len(views) - 1, "componentType": index_type, "count": len(triangles) * 3,
                          "type": "SCALAR"})
        prim["indices"] = len(accessors) - 1
        binary += idx
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [dict({"mesh": 0}, **(node or {}))], "meshes": [{"name": "m", "primitives": [prim]}],
           "accessors": accessors, "bufferViews": views}
    if textures:
        png = encode_png(1, 1, 3, b"\x80\x80\x80")
        images = []
        for _ in range(2):
            views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(png)})
            images.append({"bufferView": len(views) - 1, "mimeType": "image/png"})
            binary += png + b"\x00" * (-len(png) % 4)
        material = {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
        if normal_map:
            material["normalTexture"] = {"index": 1}
        doc.update(images=images, textures=[{"source": 0}, {"source": 1}], materials=[material])
        prim["material"] = 0
    doc["buffers"] = [{"byteLength": len(binary)}]
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return b"glTF" + struct.pack("<II", version, 12 + len(body)) + body


def write(tmp_path, data, name="m.glb"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def verdicts(glb, cls=PICKUP, overlap_max=0.005):
    return {r.id: r for r in model.check(glb, cls, "out/model.glb", overlap_max)}


# --- reader -------------------------------------------------------------------------

def test_cube_reads_closed_with_bounds_and_images(tmp_path):
    g = read(write(tmp_path, glb_bytes()))
    assert (g.triangle_count, g.vertex_count, len(g.images)) == (12, 8, 2)
    assert g.bounds() == ((-1, -1, -1), (1, 1, 1))
    assert set(edge_use(g).values()) == {2}


@pytest.mark.parametrize("index_type", [5125, 5123])
def test_both_index_widths(tmp_path, index_type):
    assert read(write(tmp_path, glb_bytes(index_type=index_type))).triangle_count == 12


def test_no_indices_means_sequential_triangles(tmp_path):
    flat = [CUBE_V[i] for t in CUBE_T for i in t]
    g = read(write(tmp_path, glb_bytes(flat, [], indices=False)))
    assert g.triangle_count == 12 and set(edge_use(g).values()) == {2}  # welded by position


def test_node_transform_is_applied(tmp_path):
    q = math.sqrt(0.5)  # 90 degrees about Y: +X goes to -Z
    node = {"translation": [10, 0, 0], "rotation": [0, q, 0, q], "scale": [2, 2, 2]}
    g = read(write(tmp_path, glb_bytes(node=node)))
    lo, hi = g.bounds()
    assert [round(x, 6) for x in lo] == [8, -2, -2] and [round(x, 6) for x in hi] == [12, 2, 2]
    p = g.primitives[0].positions[1]  # (1, -1, -1) -> scale 2, rotate, translate
    assert [round(c, 6) for c in p] == [8, -2, -2]


def test_matrix_node_is_applied(tmp_path):
    m = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 5, 0, 1]
    assert read(write(tmp_path, glb_bytes(node={"matrix": m}))).bounds()[0] == (-1, 4, -1)


@pytest.mark.parametrize("data, match", [
    (b"glTF" + struct.pack("<II", 2, 999) + b"\x00" * 8, "header says"),
    (b"glTF" + struct.pack("<II", 2, 12), "truncated"),
    (b"JUNKJUNKJUNKJUNKJUNK", "magic"),
    (glb_bytes(version=1), "version 1"),
    (glb_bytes(mode=1), "only triangles"),
])
def test_unreadable_glb_raises(tmp_path, data, match):
    with pytest.raises(GlbError, match=match):
        read(write(tmp_path, data))


def test_index_out_of_range_raises(tmp_path):
    with pytest.raises(GlbError, match="past the vertex list"):
        read(write(tmp_path, glb_bytes(triangles=CUBE_T + [(0, 1, 99)])))


# --- model checks: synthetic ------------------------------------------------------

def test_closed_textured_cube_passes(tmp_path):
    v = verdicts(read(write(tmp_path, glb_bytes())))
    assert all(r.passed for r in v.values()), {k: r.message for k, r in v.items()}


def test_open_mesh_fails_closed(tmp_path):
    v = verdicts(read(write(tmp_path, glb_bytes(triangles=CUBE_T[:-2]))))
    assert not v["model.closed"].passed and v["model.closed"].metrics["open_edges"] == 4


def test_fin_fails_manifold(tmp_path):
    verts = CUBE_V + [(0, -1, -1)]
    v = verdicts(read(write(tmp_path, glb_bytes(verts, CUBE_T + [(0, 1, 8)]))))
    assert not v["model.manifold"].passed


def test_triangle_budget_boundary(tmp_path):
    g = read(write(tmp_path, glb_bytes()))
    at = C.asset_class("pickup").__class__(**dict(PICKUP.__dict__, tris_max=12))
    under = C.asset_class("pickup").__class__(**dict(PICKUP.__dict__, tris_max=11))
    assert verdicts(g, at)["model.triangles"].passed
    assert not verdicts(g, under)["model.triangles"].passed


def test_vertex_budget_is_only_a_warning(tmp_path):
    g = read(write(tmp_path, glb_bytes()))
    tight = PICKUP.__class__(**dict(PICKUP.__dict__, verts_max=7))
    r = verdicts(g, tight)["model.vertices"]
    assert not r.passed and r.severity.value == "warning"


def test_uv_missing_and_outside(tmp_path):
    assert not verdicts(read(write(tmp_path, glb_bytes(uvs=False))))["model.uv"].passed
    uv = [(0.5, 0.5)] * 7 + [(1.01, 0.5)]
    r = verdicts(read(write(tmp_path, glb_bytes(uvs=uv), "o.glb")))["model.uv"]
    assert not r.passed and r.metrics["uvs_outside"] == 1
    edge = [(0.5, 0.5)] * 7 + [(1.0, 0.0)]
    assert verdicts(read(write(tmp_path, glb_bytes(uvs=edge), "e.glb")))["model.uv"].passed


def test_missing_textures_fail(tmp_path):
    assert not verdicts(read(write(tmp_path, glb_bytes(textures=False))))["model.textures"].passed


def test_a_base_colour_without_a_normal_map_fails(tmp_path):
    # Found by the step 3.8 mutation pass: "normal map optional" survived.
    r = verdicts(read(write(tmp_path, glb_bytes(normal_map=False))))["model.textures"]
    assert not r.passed and r.message == "Missing a normal map."


def test_the_smallest_hole_fails_closed(tmp_path):
    # Boundary: one missing triangle (3 open edges) is the smallest hole a mesh can have.
    v = verdicts(read(write(tmp_path, glb_bytes(triangles=CUBE_T[:-1]))))
    assert not v["model.closed"].passed and v["model.closed"].metrics["open_edges"] == 3


# --- model checks: recorded TRELLIS.2 output -----------------------------------------

def test_recorded_gem_passes_at_the_pickup_budget():
    # Only a warning: a hidden inner shell (118 tris), which the Maya winding fix deletes.
    g = read(GEN / "gem_model.glb")
    v = verdicts(g)
    assert [k for k, r in v.items() if not r.passed] == ["model.interior_shells"]
    assert not any(r.blocking for r in v.values())
    assert g.triangle_count <= 270  # 0.9 x pickup's 300
    lo, hi = g.bounds()
    assert max(h - l for l, h in zip(lo, hi)) == pytest.approx(1.0, abs=0.05)  # image-to-3D: ~1 unit, no real scale


def test_recorded_coin_fails_uv_overlap_only():
    # Same workflow, unlucky unwrap: 42 triangles share texels. Maya's
    # polyUVOverlap counts the same 42 on the ingested scene.
    g = read(GEN / "coin_model.glb")
    v = verdicts(g)
    assert [k for k, r in v.items() if not r.passed] == ["model.uv_overlap"]
    assert v["model.uv_overlap"].metrics["overlapping_triangles"] == 42
    assert v["model.uv_overlap"].metrics["overlap_share"] == pytest.approx(0.025, abs=0.001)
    assert g.triangle_count == 270


def test_overlap_tolerance_is_a_share_of_the_used_area():
    g = read(GEN / "coin_model.glb")  # shares 2.50 %
    assert not verdicts(g, overlap_max=0.024)["model.uv_overlap"].passed
    assert verdicts(g, overlap_max=0.026)["model.uv_overlap"].passed


def test_overlap_area_is_exact():
    from validate.core.glb import Glb, Primitive, overlap_area, uv_overlap
    a = [(0, 0), (1, 0), (0, 1)]
    b = [(0, 0), (0.5, 0), (0, 0.5)]  # fully inside a: shares 0.125
    g = Glb({}, [Primitive("m", [(0, 0, 0)] * 6, [(0, 1, 2), (3, 4, 5)], a + b)])
    pairs, used = uv_overlap(g)
    assert overlap_area(g, pairs) == pytest.approx(0.125) and used == pytest.approx(0.625)


def test_recorded_ring_coin_fails_closed():
    # The first attempt: 19.6M triangles decimated to 270 in one step lost both faces.
    v = verdicts(read(GEN / "coin_ring_model.glb"))
    assert not v["model.closed"].passed and v["model.closed"].metrics["open_edges"] > 0


# --- UV overlap ---------------------------------------------------------------------

def test_shared_edges_are_not_overlap(tmp_path):
    # Two triangles of one quad share an edge in UV: touching, not overlapping.
    from validate.core.glb import Glb, Primitive, uv_overlap
    g = Glb({}, [Primitive("m", [(0, 0, 0)] * 4, [(0, 1, 2), (0, 2, 3)], [(0, 0), (1, 0), (1, 1), (0, 1)])])
    pairs, area = uv_overlap(g)
    assert pairs == [] and area == pytest.approx(1.0)


def test_stacked_triangles_overlap():
    from validate.core.glb import Glb, Primitive, uv_overlap
    uv = [(0, 0), (0.5, 0), (0, 0.5)]
    g = Glb({}, [Primitive("m", [(0, 0, 0)] * 6, [(0, 1, 2), (3, 4, 5)], uv + [(0.1, 0.1), (0.3, 0.1), (0.1, 0.3)])])
    assert uv_overlap(g)[0] == [(0, 1)]


def test_barely_overlapping_triangles_are_caught():
    from validate.core.glb import Glb, Primitive, uv_overlap
    a = [(0, 0), (0.5, 0), (0, 0.5)]
    b = [(0.24, 0.24), (0.6, 0.24), (0.24, 0.6)]   # its corner pokes 0.01 into a's hypotenuse
    c = [(0.26, 0.26), (0.6, 0.26), (0.26, 0.6)]   # just clear of it
    g1 = Glb({}, [Primitive("m", [(0, 0, 0)] * 6, [(0, 1, 2), (3, 4, 5)], a + b)])
    g2 = Glb({}, [Primitive("m", [(0, 0, 0)] * 6, [(0, 1, 2), (3, 4, 5)], a + c)])
    assert uv_overlap(g1)[0] == [(0, 1)] and uv_overlap(g2)[0] == []
