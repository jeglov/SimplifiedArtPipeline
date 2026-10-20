"""gen.core.concept (is a concept image usable for image-to-3D?) and style templates.

Pure Python, <1 s. Synthetic 64 px images on a 64-cell grid, so one pixel is
one cell and every boundary is exact.
"""
from __future__ import annotations

import dataclasses

import pytest

from gen.core import concept
from gen.core import rules as gr
from validate.core.conventions import ConventionError
from validate.core.image import Pixels

N = 64
BG = (229, 228, 228)
RULES = dataclasses.replace(gr.load().concept, grid=N)


def canvas(boxes=(), bg=BG, color=(240, 180, 40), mask_only=(), image_only=()):
    """Image + mask. `boxes` (x0, y0, x1, y1) are object in both; `mask_only`
    only in the mask; `image_only` only in the image (a shadow BiRefNet drops)."""
    img = [[bg] * N for _ in range(N)]
    msk = [[0] * N for _ in range(N)]
    for (x0, y0, x1, y1) in boxes:
        for y in range(y0, y1):
            for x in range(x0, x1):
                img[y][x], msk[y][x] = color, 255
    for (x0, y0, x1, y1) in image_only:
        for y in range(y0, y1):
            for x in range(x0, x1):
                img[y][x] = (150, 140, 160)
    for (x0, y0, x1, y1) in mask_only:
        for y in range(y0, y1):
            for x in range(x0, x1):
                msk[y][x] = 255
    image = Pixels(N, N, 3, bytes(v for row in img for c in row for v in c))
    mask = Pixels(N, N, 1, bytes(v for row in msk for v in row))
    return image, mask


def verdicts(image, mask, rules=RULES):
    return {r.id: r for r in concept.check(image, mask, rules, "out/image.png")}


def test_centred_object_on_a_plain_background_passes():
    v = verdicts(*canvas([(16, 16, 48, 48)]))
    assert all(r.passed for r in v.values()), {k: r.message for k, r in v.items()}
    assert v["concept.coverage"].metrics["coverage"] == pytest.approx(0.25)
    assert v["concept.background"].metrics["background_rgb"] == list(BG)


def test_a_shadow_reaching_the_edge_in_the_image_but_not_the_mask_passes():
    # Probed on the first generated coin: its contact shadow ran to the edge.
    v = verdicts(*canvas([(16, 16, 48, 48)], image_only=[(30, 48, 63, 54)]))
    assert v["concept.margin"].passed and v["concept.single_object"].passed


def test_margin_boundary():
    need = RULES.margin_min * N  # 1.28 cells
    ok = verdicts(*canvas([(2, 16, 40, 48)]))["concept.margin"]
    assert ok.passed and 2 >= need
    bad = verdicts(*canvas([(1, 16, 40, 48)]))["concept.margin"]
    assert not bad.passed and "cut off" in bad.message


def test_margin_boundary_on_the_right_and_bottom_edges():
    # The far edges use n - 1 - max: last cell index N-1, so one free column is a margin of 1.
    assert not verdicts(*canvas([(24, 16, N - 1, 48)]))["concept.margin"].passed   # 1 free column
    assert verdicts(*canvas([(24, 16, N - 2, 48)]))["concept.margin"].passed       # 2 free columns
    assert not verdicts(*canvas([(16, 24, 48, N - 1)]))["concept.margin"].passed   # 1 free row


def test_object_touching_the_edge_fails_margin():
    assert not verdicts(*canvas([(16, 0, 48, 30)]))["concept.margin"].passed


def test_coverage_bounds():
    small = verdicts(*canvas([(30, 30, 34, 34)]))["concept.coverage"]
    assert not small.passed and "too small" in small.message
    big = verdicts(*canvas([(2, 2, 62, 62)]))["concept.coverage"]
    assert not big.passed and "too big" in big.message


def test_coverage_boundary_is_inclusive():
    cells = int(RULES.coverage_min * N * N) + 1  # 246 cells at 0.06
    side_w = 41
    rows = -(-cells // side_w)
    box = [(10, 10, 10 + side_w, 10 + rows)]
    assert verdicts(*canvas(box))["concept.coverage"].passed
    exact = side_w * rows / float(N * N)  # not metrics["coverage"]: reports round to 6 decimals
    assert verdicts(*canvas(box), rules=dataclasses.replace(RULES, coverage_min=exact))["concept.coverage"].passed
    above = dataclasses.replace(RULES, coverage_min=exact + 1e-9)
    assert not verdicts(*canvas(box), rules=above)["concept.coverage"].passed


def test_two_objects_fail_single_object():
    v = verdicts(*canvas([(8, 20, 28, 40), (36, 20, 56, 40)]))["concept.single_object"]
    assert not v.passed and v.metrics["regions"] == 2 and v.metrics["largest_share"] == pytest.approx(0.5)


def test_specks_below_the_limit_are_ignored():
    speck = RULES.speck_cells - 1
    v = verdicts(*canvas([(16, 16, 48, 48)], mask_only=[(55, 10, 55 + speck, 11)]))["concept.single_object"]
    assert v.passed and v.metrics["regions"] == 1
    v = verdicts(*canvas([(16, 16, 48, 48)], mask_only=[(55, 10, 55 + RULES.speck_cells, 11)]))
    assert v["concept.single_object"].metrics["regions"] == 2


def test_diagonal_touch_is_two_regions():
    # 4-connectivity: corners touching don't join regions.
    sizes = concept.components([[True, False], [False, True]])
    assert sizes == [1, 1]


def test_scene_background_fails():
    image, mask = canvas([(16, 16, 48, 48)])
    data = bytearray(image.data)
    for y in range(N):           # left half of the frame is a different colour: a scene
        for x in range(N // 2):
            data[(y * N + x) * 3:(y * N + x) * 3 + 3] = bytes((60, 120, 60))
    v = verdicts(Pixels(N, N, 3, bytes(data)), mask)["concept.background"]
    assert not v.passed and v.metrics["uniformity"] < RULES.background_uniformity_min


def test_empty_mask_fails_cleanly():
    v = verdicts(*canvas())
    assert not v["concept.margin"].passed and v["concept.margin"].message == "No object found."
    assert not v["concept.single_object"].passed and not v["concept.coverage"].passed


def test_failing_checks_name_the_image():
    v = verdicts(*canvas([(16, 0, 48, 30)]))["concept.margin"]
    assert v.offenders == ("out/image.png",)


def test_mask_of_another_size_is_refused():
    image, _ = canvas([(16, 16, 48, 48)])
    with pytest.raises(ValueError, match="mask is"):
        concept.check(image, Pixels(32, 32, 1, bytes(32 * 32)), RULES, "x")


# --- style templates ----------------------------------------------------------------

STYLES = gr.load_styles(class_names=("pickup", "prop_small", "prop_medium"))


def test_prompt_is_style_plus_class_plus_framing():
    p = STYLES.prompt("  a golden   coin ", "pickup", "casual_toon")
    assert p.startswith("a golden coin. Stylized 3D game asset")
    assert STYLES.classes["pickup"] in p and p.endswith(STYLES.framing)
    assert "no cast shadow" in p and "\n" not in p


def test_every_shipped_style_renders_for_every_framed_class():
    for style in STYLES.styles:
        for cls in STYLES.classes:
            assert "{" not in STYLES.prompt("x", cls, style)


@pytest.mark.parametrize("args, match", [
    (("x", "pickup", "noir"), "unknown style"),
    (("x", "character", "casual_toon"), "no concept framing"),
    (("a {subject}", "pickup", "casual_toon"), "braces"),
    (("", "pickup", "casual_toon"), "1-300"),
    (("x" * 301, "pickup", "casual_toon"), "1-300"),
])
def test_bad_prompt_requests_are_refused(args, match):
    with pytest.raises(ConventionError, match=match):
        STYLES.prompt(*args)


def styles_data(**over):
    data = {"version": 1, "framing": "f.", "classes": {"pickup": "A pickup."},
            "styles": {"toon": {"description": "d", "prompt": "{subject}. toon"}}}
    data.update(over)
    return data


@pytest.mark.parametrize("over, match", [
    ({"styles": {"toon": {"description": "d", "prompt": "{subject} {subject}"}}}, "exactly once"),
    ({"styles": {"toon": {"description": "d", "prompt": "{subject} {colour}"}}}, "exactly once"),
    ({"styles": {"Toon": {"description": "d", "prompt": "{subject}"}}}, "snake_case"),
    ({"styles": {}}, "at least one"),
    ({"classes": {"vehicle_xl": "A car."}}, "not an asset class"),
    ({"extra": 1}, "unknown key"),
])
def test_bad_style_files_fail_at_load(over, match):
    with pytest.raises(ConventionError, match=match):
        gr.parse_styles(styles_data(**over), class_names=("pickup",))


def test_concept_rules_are_validated():
    data = {"version": 1, "comfyui": {"version": "0.42.1", "commit": "a" * 40, "port": 8188,
                                      "startup_timeout_s": 1, "run_timeout_s": 1}, "models": {},
            "model": {"face_budget_share": 0.9, "remesh_resolution": 128, "uv_overlap_max_share": 0.005},
            "ingest": {"size_cm": {"pickup": 40}},
            "repair": {"max_attempts": 3, "seed_step": 1000, "default": "stop", "actions": {}}}
    good = dict(grid=64, mask_threshold=128, background_tolerance=28, background_uniformity_min=0.97,
                coverage_min=0.06, coverage_max=0.7, margin_min=0.02, single_object_min=0.95, speck_cells=4)
    assert gr.parse(dict(data, concept=good)).concept.grid == 64
    for key, value, match in [("coverage_min", 0.8, "coverage_min must be <"), ("margin_min", 1.5, "between 0 and 1"),
                              ("mask_threshold", 0, "1-255"), ("grid", 0, "> 0")]:
        with pytest.raises(ConventionError, match=match):
            gr.parse(dict(data, concept=dict(good, **{key: value})))


# --- recorded generator output (Z-Image-Turbo + BiRefNet, 256 px copies) -----------

GEN = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "gen"


def recorded(name):
    from validate.core.image import decode_png
    return decode_png(GEN / (name + "_image.png")), decode_png(GEN / (name + "_mask.png"))


def test_recorded_coin_passes_with_the_shipped_thresholds():
    # Its contact shadow reaches 6% from the right edge by colour; the mask drops it.
    v = {r.id: r for r in concept.check(*recorded("coin"), gr.load().concept, "coin")}
    assert all(r.passed for r in v.values()), {k: r.message for k, r in v.items()}
    assert v["concept.margin"].metrics["margin"] > 0.1


def test_recorded_three_apples_fail_single_object_only():
    v = {r.id: r for r in concept.check(*recorded("apples"), gr.load().concept, "apples")}
    assert [k for k, r in v.items() if not r.passed] == ["concept.single_object"]
    assert v["concept.single_object"].metrics["regions"] == 3
