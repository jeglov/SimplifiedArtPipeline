"""Tests for the conventions files and their loader - no Maya required.

Two jobs:
  1. The REAL conventions/ files load and mean what we think they mean.
  2. The loader rejects broken files with a precise message (tested by
     copying conventions/ to a temp dir and breaking one thing at a time).
"""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from validate.core.conventions import DEFAULT_DIR, ConventionError, load
from validate.core.naming import NamingRules
from validate.core.result import Severity

CONV = load()
SCHEMA = json.loads((DEFAULT_DIR / "naming.schema.json").read_text(encoding="utf-8"))


# --- naming: the schema's own examples are the test data --------------------

_VALID = [(k, ex) for k, d in SCHEMA["$defs"].items() for ex in d["examples"]]
_INVALID = [(k, ex) for k, d in SCHEMA["$defs"].items() for ex in d["x-invalid-examples"]]


@pytest.mark.parametrize("kind,name", _VALID)
def test_schema_examples_are_valid(kind, name):
    assert CONV.naming.check(kind, name).ok


@pytest.mark.parametrize("kind,name", _INVALID)
def test_schema_invalid_examples_are_rejected(kind, name):
    res = CONV.naming.check(kind, name)
    assert not res.ok
    # The message must give the agent something actionable.
    assert "e.g." in res.message and kind in res.message


def test_trailing_newline_is_rejected():
    # re.match('^...$') would accept this; fullmatch must not.
    assert not CONV.naming.check("mesh_node", "SM_Crate\n").ok


def test_unknown_name_kind_lists_options():
    with pytest.raises(KeyError, match="mesh_node"):
        CONV.naming.check("mesh", "SM_Crate")


@pytest.mark.parametrize("name,level,base", [
    ("SM_Crate_A_LOD2", 2, "SM_Crate_A"),
    ("SM_Crate_LOD0", 0, "SM_Crate"),
    ("SM_Crate_A", None, "SM_Crate_A"),
])
def test_lod_parsing(name, level, base):
    assert NamingRules.lod_level(name) == level
    assert NamingRules.strip_lod(name) == base


# --- budgets: inheritance and lookups ---------------------------------------

def test_units_are_cm():
    assert CONV.linear_unit == "cm"


def test_expected_classes():
    assert CONV.class_names == ("character", "crowd_unit", "env_module", "pickup",
                                "prop_hero", "prop_medium", "prop_small", "vehicle")


def test_defaults_are_inherited_and_overridden():
    small, hero, env = (CONV.asset_class(n) for n in ("prop_small", "prop_hero", "env_module"))
    assert small.lods.max == 1 and hero.lods.max == 3  # override
    assert small.lods.max_ratio == hero.lods.max_ratio  # inherited
    assert env.uv.unit_square is False and env.uv.required_sets == ("map1",)  # partial dict merge
    assert CONV.asset_class("pickup").pivot.mode == "center"
    assert CONV.asset_class("pickup").pivot.tolerance_cm == 0.5  # sibling key kept
    assert CONV.asset_class("character").mesh_types == ("SK",)


def test_severity_overrides():
    env, small = CONV.asset_class("env_module"), CONV.asset_class("prop_small")
    assert env.severity_for("topology.open_borders", Severity.ERROR) is Severity.WARNING
    assert small.severity_for("topology.open_borders", Severity.ERROR) is Severity.ERROR


def test_texture_size_per_class():
    assert CONV.asset_class("crowd_unit").texture_max_size == 256
    assert CONV.asset_class("prop_hero").texture_max_size == 1024


def test_lod_tri_limit():
    # LOD1 of a 1000-tri LOD0: 1000 * 0.5 * 1.10 = 550
    assert CONV.asset_class("prop_hero").lods.max_tris(1, 1000) == 550


def test_unknown_class_lists_options():
    with pytest.raises(KeyError, match="prop_small"):
        CONV.asset_class("prop_tiny")


def test_normal_maps_are_opengl_linear():
    n = {t.suffix: t for t in CONV.textures.types}["N"]
    assert n.color_space == "linear" and n.alpha == "forbidden"


# --- loader rejects broken files ---------------------------------------------

@pytest.fixture
def conv_copy(tmp_path):
    dst = tmp_path / "conventions"
    shutil.copytree(DEFAULT_DIR, dst)
    return dst


def _edit_yaml(path: Path, mutate):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d["classes"]["prop_small"].update(tri_max=500), r"prop_small: unknown key\(s\) \['tri_max'\]"),
    (lambda d: d["classes"]["prop_small"].pop("tris_max"), r"prop_small\.tris_max: missing"),
    (lambda d: d["classes"]["prop_small"].update(tris_max=True), r"tris_max: expected int, got bool"),
    (lambda d: d["classes"]["pickup"]["pivot"].update(mode="top"), r"pivot\.mode"),
    (lambda d: d["classes"]["pickup"].update(size_cm={"min": 10, "max": 5}), r"size_cm"),
    (lambda d: d["classes"]["pickup"].update(lods={"max_ratio": [1.0, 0.6, 0.7, 0.1]}), r"never increase"),
    (lambda d: d["classes"]["pickup"].update(severity_overrides={"topology.ngons": "fatal"}), r"bad severity"),
    (lambda d: d["classes"]["pickup"].update(severity_overrides={"ngons": "warning"}), r"bad check id"),
    (lambda d: d["classes"]["pickup"].update(mesh_types=["SKM"]), r"mesh_types"),
    (lambda d: d.update(version=2), r"version must be 1"),
])
def test_bad_budgets_rejected(conv_copy, mutate, expected):
    _edit_yaml(conv_copy / "mesh_budgets.yaml", mutate)
    with pytest.raises(ConventionError, match=expected):
        load(conv_copy)


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d["max_size_by_class"].update(prop_tiny=256), r"unknown classes \['prop_tiny'\]"),
    (lambda d: d["max_size_by_class"].update(pickup=100), r"power of two"),
    (lambda d: d["types"].pop("AO"), r"texture suffixes differ"),
    (lambda d: d["types"]["N"].update(color_space="gamma"), r"color_space"),
])
def test_bad_texture_rules_rejected(conv_copy, mutate, expected):
    _edit_yaml(conv_copy / "texture_rules.yaml", mutate)
    with pytest.raises(ConventionError, match=expected):
        load(conv_copy)


def test_loading_is_deterministic():
    assert load().classes == load().classes
