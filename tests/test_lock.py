"""conventions.lock.json: fresh, deterministic, resolved, and traceable to its sources."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from validate.core import conventions as conv
from validate.core import lock

LOCK_PATH = conv.DEFAULT_DIR / lock.LOCK_NAME


@pytest.fixture
def conv_copy(tmp_path):
    """A writable copy of conventions/ (with its lock) to break on purpose."""
    d = tmp_path / "conventions"
    shutil.copytree(str(conv.DEFAULT_DIR), str(d))
    return d


def edit_yaml(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    with path.open("w", encoding="utf-8", newline="\n") as fh:  # write_text has no newline= in 3.9
        fh.write(yaml.safe_dump(data, sort_keys=False))


def test_committed_lock_is_fresh():
    # THE stale-lock test: if this fails, run `artpipe conventions --dump`.
    assert LOCK_PATH.read_text(encoding="utf-8") == lock.render(), \
        "conventions.lock.json is stale. Run: artpipe conventions --dump"


def test_check_command_agrees():
    assert lock.main(["--check"]) == 0


def test_render_is_deterministic():
    assert lock.render() == lock.render()


def test_lock_is_canonical_lf_json():
    raw = LOCK_PATH.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8") == lock.to_json(json.loads(raw))


def test_any_source_edit_makes_the_lock_stale(conv_copy):
    assert lock.main(["--check", "--dir", str(conv_copy)]) == 0
    edit_yaml(conv_copy / "mesh_budgets.yaml",
              lambda d: d["classes"]["prop_small"].__setitem__("tris_max", 501))
    assert lock.main(["--check", "--dir", str(conv_copy)]) == 1
    assert lock.main(["--dump", "--dir", str(conv_copy)]) == 0
    assert lock.main(["--check", "--dir", str(conv_copy)]) == 0


def test_comment_only_edit_still_changes_sources_hash(conv_copy):
    # Values unchanged, bytes changed: the lock records it, so a reader can
    # always trace the lock to the exact source files.
    p = conv_copy / "texture_rules.yaml"
    p.write_bytes(p.read_bytes() + b"# a comment\n")
    assert lock.main(["--check", "--dir", str(conv_copy)]) == 1


def test_missing_lock_is_stale(conv_copy):
    (conv_copy / lock.LOCK_NAME).unlink()
    assert lock.main(["--check", "--dir", str(conv_copy)]) == 1


def test_invalid_conventions_are_a_tool_error_and_write_nothing(conv_copy):
    before = (conv_copy / lock.LOCK_NAME).read_bytes()
    edit_yaml(conv_copy / "mesh_budgets.yaml",
              lambda d: d["classes"]["prop_small"].__setitem__("tri_max", 1))  # typo
    assert lock.main(["--dump", "--dir", str(conv_copy)]) == 2
    assert (conv_copy / lock.LOCK_NAME).read_bytes() == before


def test_sources_hash_the_exact_files():
    data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert sorted(data["sources"]) == sorted(lock.SOURCES)
    for name, digest in data["sources"].items():
        assert digest == hashlib.sha256((conv.DEFAULT_DIR / name).read_bytes()).hexdigest()


def test_classes_are_resolved_with_defaults_merged():
    data = json.loads(lock.render())
    c = conv.load()
    assert sorted(data["classes"]) == sorted(c.class_names)
    for k in c.classes:
        got = data["classes"][k.name]
        assert got["tris_max"] == k.tris_max
        assert got["lods"]["max"] == k.lods.max
        assert got["lods"]["max_ratio"] == list(k.lods.max_ratio)  # inherited from defaults
        assert got["pivot"]["mode"] == k.pivot.mode
        assert got["texture_max_size"] == k.texture_max_size
        assert got["uv"]["unit_square"] == k.uv.unit_square


def test_texture_formats_are_resolved_per_map_type():
    types = json.loads(lock.render())["textures"]["types"]
    # The normal-map override wins; every other type keeps the default.
    assert types["N"]["formats"] == {"android": "ASTC_4x4", "ios": "ASTC_4x4"}
    for suffix in ("D", "MS", "AO", "E"):
        assert types[suffix]["formats"] == {"android": "ASTC_6x6", "ios": "ASTC_6x6"}


def test_naming_rules_carry_their_examples():
    naming = json.loads(lock.render())["naming"]
    assert set(naming) == {"group_node", "material", "mesh_file", "mesh_node", "texture_file"}
    for rule in naming.values():
        assert rule["examples"] and rule["invalid_examples"]


# --- import_hints validation (typed since Phase 2) --------------------------------

def hints_error(conv_copy, change) -> str:
    edit_yaml(conv_copy / "texture_rules.yaml", lambda d: change(d["import_hints"]))
    with pytest.raises(conv.ConventionError) as exc:
        conv.load(conv_copy)
    return str(exc.value)


def test_unknown_format_is_rejected(conv_copy):
    msg = hints_error(conv_copy, lambda h: h["android"].__setitem__("format", "ASTC_7x7"))
    assert "import_hints.android.format" in msg


def test_unknown_platform_in_override_is_rejected(conv_copy):
    msg = hints_error(conv_copy, lambda h: h["overrides"]["N"].__setitem__("webgl", {"format": "ASTC_4x4"}))
    assert "unknown platform" in msg


def test_override_for_unknown_map_type_is_rejected(conv_copy):
    msg = hints_error(conv_copy, lambda h: h["overrides"].__setitem__("X", {"android": {"format": "ASTC_4x4"}}))
    assert "unknown map type" in msg


def test_missing_platform_default_is_rejected(conv_copy):
    msg = hints_error(conv_copy, lambda h: h.pop("ios"))
    assert "import_hints.ios: missing required key" in msg


def test_unknown_hint_key_is_rejected(conv_copy):
    msg = hints_error(conv_copy, lambda h: h.__setitem__("mipmap", True))  # typo of mipmaps
    assert "unknown key" in msg


# --- fbx_export.yaml (Phase 2 step 3) -------------------------------------------------

def fbx_error(conv_copy, change) -> str:
    edit_yaml(conv_copy / "fbx_export.yaml", change)
    with pytest.raises(conv.ConventionError) as exc:
        conv.load(conv_copy)
    return str(exc.value)


def test_fbx_export_is_in_the_lock():
    f = json.loads(lock.render())["fbx_export"]
    c = conv.load().fbx_export
    assert f["settings_version"] == c.settings_version
    assert sorted(f["flags"]) == sorted(conv.FBX_FLAGS)


def test_binary_fbx_export_is_rejected(conv_copy):
    assert "must be true" in fbx_error(conv_copy, lambda d: d.__setitem__("ascii", False))


def test_every_fbx_flag_is_required(conv_copy):
    # Adding an exporter setting without deciding its value is a load error.
    assert "cameras: missing required key" in fbx_error(conv_copy, lambda d: d.pop("cameras"))


def test_unknown_fbx_setting_is_rejected(conv_copy):
    assert "unknown key" in fbx_error(conv_copy, lambda d: d.__setitem__("camera", False))


def test_bad_fbx_file_version_is_rejected(conv_copy):
    assert "file_version" in fbx_error(conv_copy, lambda d: d.__setitem__("file_version", "7.7"))


def test_fbx_settings_version_starts_at_one(conv_copy):
    # Boundary: 0 is invalid, 1 is the first valid version.
    assert "settings_version" in fbx_error(conv_copy, lambda d: d.__setitem__("settings_version", 0))


# --- unity_import.yaml (Phase 2 step 4) ----------------------------------------------

@pytest.mark.parametrize("root", ["Assets", "Art", "Assets/Art/", "assets/Art", "Assets\Art", "Assets//Art"])
def test_bad_unity_root_is_rejected(conv_copy, root):
    edit_yaml(conv_copy / "unity_import.yaml", lambda d: d.__setitem__("root", root))
    with pytest.raises(conv.ConventionError, match="unity_import.root"):
        conv.load(conv_copy)


def test_unity_root_is_in_the_lock():
    assert json.loads(lock.render())["unity_import"] == {"root": conv.load().unity_import.root}
