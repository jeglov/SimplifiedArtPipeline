"""validate.core.winding (#43), validate.core.texel (#13), gen.core.ingest. Pure Python, <1 s.

The Maya checks built on them are exercised on real scenes by the fixtures
(SM_FlippedFaces, SM_FlippedShell, SM_TexelStretch, SM_ZeroUVFace).
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from gen.core import ingest
from validate.core import texel, winding
from validate.core.glb import Glb, GlbError, Primitive, read

GEN = Path(__file__).resolve().parent / "fixtures" / "gen"

# Cube, outward (counter-clockwise seen from outside), quads.
PTS = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
CUBE = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [0, 4, 7, 3]]


def rev(face):
    return list(reversed(face))


# --- winding --------------------------------------------------------------------------

def test_consistent_cube_is_one_clean_outward_shell():
    (s,) = winding.shells(CUBE)
    assert s.flipped == [] and s.orientable and len(s.faces) == 6
    assert winding.signed_volume(PTS, CUBE) == pytest.approx(48.0)  # 6 x 8
    assert winding.open_edges(CUBE) == 0


@pytest.mark.parametrize("bad", [[0], [3], [0, 3], [1, 2, 4]])
def test_minority_faces_are_reported(bad):
    faces = [rev(f) if i in bad else f for i, f in enumerate(CUBE)]
    (s,) = winding.shells(faces)
    assert s.flipped == sorted(bad) and s.orientable


def test_majority_flipped_reports_the_good_minority():
    # 4 of 6 reversed: the 2 untouched faces are the minority. Which side is
    # outside is topology.inverted's question, not this one.
    faces = [rev(f) if i in (0, 1, 2, 3) else f for i, f in enumerate(CUBE)]
    assert winding.shells(faces)[0].flipped == [4, 5]


def test_tie_keeps_the_seed_side():
    faces = [rev(f) if i in (1, 2, 4) else f for i, f in enumerate(CUBE)]
    assert winding.shells(faces)[0].flipped == [1, 2, 4]


def test_inside_out_shell_is_consistent_but_has_negative_volume():
    inside_out = [rev(f) for f in CUBE]
    (s,) = winding.shells(inside_out)
    assert s.flipped == []
    assert winding.signed_volume(PTS, inside_out) == pytest.approx(-48.0)


def test_two_shells_are_judged_separately():
    second = [[v + 8 for v in rev(f)] for f in CUBE]
    shells = winding.shells(CUBE + second)
    assert [len(s.faces) for s in shells] == [6, 6] and all(not s.flipped for s in shells)


def test_moebius_strip_is_not_orientable():
    # 6 quads around a ring, the last one joined with a half twist.
    n = 6
    top = list(range(n))
    bottom = list(range(n, 2 * n))
    faces = [[top[i], top[i + 1], bottom[i + 1], bottom[i]] for i in range(n - 1)]
    faces.append([top[n - 1], bottom[0], top[0], bottom[n - 1]])  # twisted closure
    assert not winding.shells(faces)[0].orientable


def test_open_and_nonmanifold_edges_do_not_propagate():
    faces = CUBE[:5]  # one face missing: open, still consistent
    assert winding.shells(faces)[0].flipped == []
    assert winding.open_edges(faces) == 4


def test_degenerate_faces_are_ignored():
    assert [len(s.faces) for s in winding.shells(CUBE + [[0, 1]])] == [6]


# --- texel density --------------------------------------------------------------------

def test_density_formula():
    # 1 m^2 of surface on the full 0-1 square at 256 px: 256 px/m.
    assert texel.density([(1.0, 1.0)], 256).px_per_m == pytest.approx(256.0)
    # A quarter of the UV square on 1 m^2: half the density.
    assert texel.density([(0.25, 1.0)], 256).px_per_m == pytest.approx(128.0)


def test_faces_more_than_one_mip_off_are_flagged():
    even = [(0.01, 0.01)] * 10
    assert texel.density(even, 256).off_faces == []
    stretched = even + [(0.01 * 0.01, 0.01)]  # 10x fewer texels per metre on one face
    assert texel.density(stretched, 256).off_faces == [10]


def test_two_x_is_the_boundary():
    mesh = [(0.01, 0.01)] * 100
    just_inside = mesh + [(0.01 * 3.9, 0.01)]   # sqrt(3.9) = 1.97x denser
    just_outside = mesh + [(0.01 * 4.2, 0.01)]  # 2.05x
    assert texel.density(just_inside, 256).off_faces == []
    assert texel.density(just_outside, 256).off_faces == [100]


def test_zero_uv_and_unmapped_faces():
    d = texel.density([(0.5, 1.0), (0.0, 1.0), (None, 1.0)], 256)
    assert d.zero_uv_faces == [1]
    assert d.px_per_m == pytest.approx(256 * math.sqrt(0.5 / 2.0))  # unmapped face left out entirely


def test_texture_px_needed_is_the_next_power_of_two():
    d = texel.density([(0.25, 1.0)], 128, target_px_per_m=200)  # 64 px/m now; size x 0.5 >= 200 -> 400 px
    assert d.texture_px_needed == 512
    assert texel.density([(0.25, 1.0)], 512).px_per_m >= 200 > texel.density([(0.25, 1.0)], 256).px_per_m
    assert texel.density([(1.0, 1.0)], 256, target_px_per_m=256).texture_px_needed == 256


def test_no_area_gives_zero_density():
    assert texel.density([(0.5, 0.0)], 256).px_per_m == 0.0


# --- ingest arrays --------------------------------------------------------------------

def tri_glb(positions, tris, uvs, normals=None):
    return Glb({}, [Primitive("m", positions, tris, uvs, normals)])


def test_weld_flip_v_and_scale_to_size():
    # Two triangles of a unit quad, split at a seam (vertex 0 and 3 share a position).
    pos = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 0, 0), (1, 1, 0), (0, 1, 0)]
    uvs = [(0, 0), (1, 0), (1, 1), (0.5, 0), (1, 1), (0, 1)]
    a = ingest.arrays(tri_glb(pos, [(0, 1, 2), (3, 4, 5)], uvs), size_cm=50, pivot_mode="bottom_center")
    assert len(a.points) == 4 and a.counts == [3, 3] and a.scale == 50
    assert a.connects[0] == a.connects[3]                        # welded across the seam
    assert len(a.us) == 5                                        # UVs stay split: the seam is in UV only
    assert (a.us[0], a.vs[0]) == (0.0, 1.0)                      # glTF v=0 is the image top -> Maya v=1
    lo, hi = ingest.bounds(a.points)
    assert lo == (-25.0, 0.0, 0.0) and hi == (25.0, 50.0, 0.0)   # bottom-centre on the origin


def test_center_mode_centres_the_bbox():
    pos = [(0, 0, 0), (2, 0, 0), (2, 1, 0)]
    a = ingest.arrays(tri_glb(pos, [(0, 1, 2)], [(0, 0)] * 3), 40, "center")
    assert ingest.bounds(a.points) == ((-20.0, -10.0, 0.0), (20.0, 10.0, 0.0))


def test_degenerate_triangles_are_dropped_and_counted():
    pos = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 0)]
    a = ingest.arrays(tri_glb(pos, [(0, 1, 2), (1, 2, 3)], [(0, 0)] * 4), 10, "center")
    assert a.dropped == 1 and a.counts == [3]


def test_normals_are_kept_per_corner():
    pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    a = ingest.arrays(tri_glb(pos, [(0, 1, 2)], [(0, 0)] * 3, [(0, 0, 1)] * 3), 10, "center")
    assert a.normals == [(0, 0, 1)] * 3


@pytest.mark.parametrize("glb, match", [
    (Glb({}, []), "no geometry"),
    (tri_glb([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], None), "UV0"),
    (tri_glb([(0, 0, 0)] * 3, [(0, 1, 2)], [(0, 0)] * 3), "flat or a point"),
])
def test_unusable_glbs_are_refused(glb, match):
    with pytest.raises(GlbError, match=match):
        ingest.arrays(glb, 10, "center")


def test_recorded_coin_ingests_at_40_cm_with_its_270_triangles():
    a = ingest.arrays(read(GEN / "coin_model.glb"), 40, "center")
    lo, hi = ingest.bounds(a.points)
    assert max(h - l for l, h in zip(lo, hi)) == pytest.approx(40.0)
    assert len(a.counts) == 270 and a.dropped == 0
    assert len(a.points) < 270  # welded: the GLB stores 264 split vertices, a closed mesh has fewer
    assert len(a.normals) == 3 * 270


def test_inward_shell_inside_an_outward_one_is_interior():
    inner_pts = [(x * 0.5, y * 0.5, z * 0.5) for x, y, z in PTS]
    pts = PTS + inner_pts
    inner = [[v + 8 for v in rev(f)] for f in CUBE]
    interior, inside_out = winding.classify_inward(pts, CUBE + inner)
    assert interior == [list(range(6, 12))] and inside_out == []


def test_inward_shell_beside_an_outward_one_is_inside_out():
    pts = PTS + [(x + 5, y, z) for x, y, z in PTS]
    second = [[v + 8 for v in rev(f)] for f in CUBE]
    interior, inside_out = winding.classify_inward(pts, CUBE + second)
    assert interior == [] and inside_out == [list(range(6, 12))]


def test_outward_nested_shell_is_left_alone():
    # A legitimate part inside another (a ball in a glass) faces outward: not interior.
    pts = PTS + [(x * 0.5, y * 0.5, z * 0.5) for x, y, z in PTS]
    nested = [[v + 8 for v in f] for f in CUBE]
    assert winding.classify_inward(pts, CUBE + nested) == ([], [])


def test_recorded_gem_has_its_hidden_inner_shell_flagged():
    from gen.core import model
    from validate.core import conventions as conv
    g = read(GEN / "gem_model.glb")
    r = {x.id: x for x in model.check(g, conv.load().asset_class("pickup"), "gem")}["model.interior_shells"]
    assert not r.passed and r.severity.value == "warning" and r.metrics["interior_triangles"] == 118
