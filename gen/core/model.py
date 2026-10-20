"""Checks on a generated GLB, before it goes to Maya: is the raw output usable at all?

  model.triangles  at or under the class budget (tris_max)
  model.vertices   GPU vertices (glTF stores them split at seams) under verts_max;
                   a warning: Unity measures the real number after import
  model.closed     no open edges: an open shell shows its inside in game
  model.manifold   no edge shared by more than two triangles
  model.uv         UV0 on every primitive, inside 0-1
  model.uv_overlap triangles share at most uv_overlap_max_share of the used UV
                   area: the textures are baked, so a shared texel shows one
                   surface's colour on another (probed: the first coin's unwrap
                   shared 2.5 %; a 2-triangle fold on the cactus 0.03 %). Metric
                   uv_area_used: share of the texture the islands cover (~50 %)
  model.interior_shells  (warning) no inward shell hidden inside another: the
                   inner wall a hollow UDF remesh leaves (probed: a gem spent 118 of
                   270 triangles on one). Not a retry: topology.winding's fix
                   deletes it deterministically in Maya
  model.textures   a base colour and a normal map are embedded

These catch a broken image-to-3D run (the first coin lost both faces at the
decimation step: 21 open edges) so the repair loop can retry generation
before Maya is started. The full Maya stage still runs on whatever passes.
Pure Python 3.9.
"""
from __future__ import annotations

from typing import List

from validate.core.conventions import AssetClass
from validate.core.glb import Glb, edge_use, overlap_area, uv_overlap, welded
from validate.core.winding import classify_inward
from validate.core.result import CheckResult, Severity

UV_TOLERANCE = 1e-4


def check(glb: Glb, cls: AssetClass, shown_as: str, uv_overlap_max_share: float = 0.0) -> List[CheckResult]:
    """`uv_overlap_max_share`: tolerated shared UV area / used UV area (generation.yaml)."""
    results = []

    def add(cid, passed, message, severity=Severity.ERROR, **metrics):
        results.append(CheckResult(cid, passed, cls.severity_for(cid, severity), message, False,
                                   metrics=metrics, offenders=() if passed else (shown_as,)))

    tris = glb.triangle_count
    add("model.triangles", 0 < tris <= cls.tris_max,
        "%d triangles, budget %d." % (tris, cls.tris_max) if tris else "No triangles.",
        triangles=tris, tris_max=cls.tris_max)

    verts = glb.vertex_count
    add("model.vertices", verts <= cls.verts_max,
        "%d GPU vertices (split at seams), budget %d." % (verts, cls.verts_max) +
        ("" if verts <= cls.verts_max else " Unity measures the final number after import."),
        severity=Severity.WARNING, vertices=verts, verts_max=cls.verts_max)

    uses = edge_use(glb)
    open_edges = sum(1 for n in uses.values() if n == 1)
    nonmanifold = sum(1 for n in uses.values() if n > 2)
    add("model.closed", open_edges == 0,
        "Closed surface." if not open_edges else
        "%d open edges: holes, so the inside shows in game (a broken decimation does this)." % open_edges,
        open_edges=open_edges)
    add("model.manifold", nonmanifold == 0,
        "Manifold." if not nonmanifold else "%d edges shared by more than two triangles." % nonmanifold,
        nonmanifold_edges=nonmanifold)

    missing_uv = [p.mesh for p in glb.primitives if not p.uvs]
    outside = sum(1 for p in glb.primitives for u, v in (p.uvs or ())
                  if not (-UV_TOLERANCE <= u <= 1 + UV_TOLERANCE and -UV_TOLERANCE <= v <= 1 + UV_TOLERANCE))
    add("model.uv", bool(glb.primitives) and not missing_uv and not outside,
        "UV0 present, inside 0-1." if glb.primitives and not missing_uv and not outside else
        "No geometry." if not glb.primitives else
        "No UV0 on %s." % ", ".join(sorted(set(missing_uv))) if missing_uv else
        "%d UVs outside 0-1: a baked texture can't tile." % outside,
        uvs_outside=outside)

    points = [v for p in glb.primitives for v in p.positions]
    weld = welded(points)
    faces, offset = [], 0
    for p in glb.primitives:
        faces += [[weld[i + offset] for i in t] for t in p.triangles]
        offset += len(p.positions)
    unique = {}
    for v, w in zip(points, weld):
        unique.setdefault(w, v)
    interior, _ = classify_inward([unique[i] for i in range(len(unique))], faces)
    hidden = sum(len(s) for s in interior)
    add("model.interior_shells", not interior,
        "No hidden inner shells." if not interior else
        "%d interior shell(s): %d triangles (%d%% of the budget) inside the asset, never visible "
        "(the inner wall of a hollow remesh). The Maya fix deletes them." % (
            len(interior), hidden, round(100.0 * hidden / cls.tris_max)),
        severity=Severity.WARNING, interior_triangles=hidden)

    pairs, uv_area = uv_overlap(glb)
    faces = sorted({f for pair in pairs for f in pair})
    shared = overlap_area(glb, pairs) / uv_area if uv_area else 0.0
    ok = shared <= uv_overlap_max_share
    add("model.uv_overlap", ok,
        "No overlapping UVs; %.0f%% of the texture used." % (100 * uv_area) if not pairs else
        "%d triangles fold over each other in UV, sharing %.2f%% of the used area (tolerated up to "
        "%.2f%%); %.0f%% of the texture used." % (len(faces), 100 * shared, 100 * uv_overlap_max_share,
                                                  100 * uv_area) if ok else
        "%d triangles overlap in UV, sharing %.2f%% of the used area (max %.2f%%): the bake writes two "
        "surfaces into the same texels." % (len(faces), 100 * shared, 100 * uv_overlap_max_share),
        overlapping_triangles=len(faces), overlap_share=shared, uv_area_used=uv_area)

    materials = glb.json.get("materials") or []
    has_base = any("baseColorTexture" in (m.get("pbrMetallicRoughness") or {}) for m in materials)
    has_normal = any("normalTexture" in m for m in materials)
    add("model.textures", has_base and has_normal and len(glb.images) >= 2,
        "Base colour and normal map embedded." if has_base and has_normal else
        "Missing %s." % " and ".join(n for n, ok in (("a base colour texture", has_base),
                                                     ("a normal map", has_normal)) if not ok),
        images=len(glb.images))
    return results
