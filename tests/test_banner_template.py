"""banners.core.template: the template as data, and its strict reader. Pure Python, <1 s."""
from __future__ import annotations

import copy

import pytest

from banners.core import template as tm
from validate.core.conventions import ConventionError, _read_yaml

PATH = tm.TEMPLATE_DIR / "hero_endcard.yaml"
RAW = _read_yaml(PATH)


def layer_index(lid):
    return next(i for i, layer in enumerate(RAW["layers"]) if layer["id"] == lid)


def edited(fn):
    data = copy.deepcopy(RAW)
    fn(data)
    return data


def test_shipped_template_loads():
    t = tm.load("hero_endcard")
    assert t.duration_s == 8.0 and [layer.id for layer in t.layers] == [
        "background", "hero", "headline", "subline", "logo", "cta"]
    assert [layer.id for layer in t.layers if layer.readable] == ["headline", "subline", "logo", "cta"]
    assert t.layer("cta").fill == "cta" and t.layer("headline").max_lines == 2
    assert t.layer("hero").tracks["scale"][0].value == 0.9


def test_unknown_template_lists_the_shipped_ones():
    with pytest.raises(ConventionError, match="hero_endcard"):
        tm.load("nope")


def _set(lid, key, value):
    def fn(data):
        data["layers"][layer_index(lid)][key] = value
    return fn


def _track(lid, prop, keys):
    def fn(data):
        data["layers"][layer_index(lid)].setdefault("tracks", {})[prop] = keys
    return fn


@pytest.mark.parametrize("change, match", [
    (_set("headline", "text", "title"), "not a declared text slot"),
    (_set("cta", "fill", None), "expected str"),
    (_set("headline", "box", [0.5, 0.1, 0.6, 0.1]), "inside the frame"),         # x + w = 1.1
    (_set("logo", "box", [-0.01, 0.1, 0.5, 0.1]), "inside the frame"),
    (_set("headline", "size", 0.5), r"\(0, 0.5\)"),
    (_set("headline", "max_lines", 0), ">= 1"),
    (_set("headline", "weight", 750), "steps of 100"),
    (_set("hero", "fit", "stretch"), "fit"),
    (_set("hero", "kind", "video"), "kind"),
    (_set("hero", "colour", "text"), "unknown key"),
    (_track("hero", "skew", [[0.0, 1.0]]), "unknown property"),
    (_track("hero", "scale", [[1.0, 1.0], [1.0, 2.0]]), "must increase"),          # boundary: equal times
    (_track("hero", "scale", [[0.0, 1.0], [8.01, 2.0]]), "outside 0-8.0"),
    (_track("hero", "scale", [[0.0, 1.0, "bounce"]]), "unknown easing"),
    (_track("hero", "scale", [[0.0, True]]), "a key is"),
    (_track("hero", "scale", []), "at least one key"),
    (lambda d: d["slots"]["colour"].append("unused"), "no layer uses"),
    (lambda d: d.__setitem__("end_card_from_s", 8.0), "inside the duration"),     # boundary: = duration
    (lambda d: d.__setitem__("id", "other"), "match the file name"),
    (lambda d: d["layers"].append(copy.deepcopy(d["layers"][0])), "unique ids"),
])
def test_mistakes_fail_at_load(change, match):
    with pytest.raises(ConventionError, match=match):
        tm.parse(edited(change), PATH)


def test_boundaries_that_still_load():
    # A readable box touching the frame edges exactly, and a key exactly at the end.
    data = edited(_set("headline", "box", [0.0, 0.0, 1.0, 0.12]))
    data = copy.deepcopy(data)
    data["layers"][layer_index("hero")]["tracks"]["scale"] = [[0.0, 1.0], [8.0, 1.2]]
    t = tm.parse(data, PATH)
    assert t.layer("headline").box == (0.0, 0.0, 1.0, 0.12) and t.layer("hero").tracks["scale"][-1].t == 8.0
    # Background and hero may bleed past the frame.
    t = tm.parse(edited(_set("hero", "box", [-0.2, 0.2, 1.4, 0.5])), PATH)
    assert t.layer("hero").box[0] == -0.2
