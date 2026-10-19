"""naming.convention: names follow naming.schema.json and the hierarchy is
shaped the way Unity expects.

Name rules (regexes come from the schema, never from this file):
  file stem, groups  <SM|SK>_<AssetName>[_Variant]          (group_node)
  meshes             <SM|SK>_<AssetName>[_Variant][_LODn]   (mesh_node)
  materials          M_<Name>[_Variant]                     (material)

Hierarchy rules (cross-node, beyond what a regex can say):
  * one root object holding all meshes, named like the file. With a single
    root, Unity's importer makes it the prefab root, and the LODGroup is
    built on it from the _LODn children.
  * every mesh is named <file stem>[_LODn]: one asset per file.
  * _LODn on all meshes or on none (Unity's LODGroup setup silently
    ignores a mix).
  * the SM_/SK_ prefix is allowed for the class (mesh_types): an SK_ crate
    would be imported as a skinned mesh.
  * no stray root objects (locators, extra cameras): FBX exports them as
    empty GameObjects. Joints may be roots: a skeleton is usually its own root.

Whether LOD levels are contiguous is budget.lods' job, not repeated here.
"""
from __future__ import annotations

from pathlib import Path

from maya import cmds

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import hierarchy, materials, roots, short_name

ID = "naming.convention"
SEVERITY = Severity.ERROR
FIXABLE = True  # see validate/maya/fixes/naming_convention.py
DESCRIPTION = "Names follow naming.schema.json; one root named like the file; consistent LOD suffixes."


def _is_startup_camera(node: str) -> bool:
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    return bool(shapes) and all(cmds.nodeType(s) == "camera" and cmds.camera(s, query=True, startupCamera=True)
                                for s in shapes)


def _example(naming, kind: str) -> str:
    examples = naming.rule(kind).examples
    return examples[0] if examples else "?"


def run(ctx) -> CheckResult:
    if not ctx.meshes:  # empty scene: scene.has_meshes reports it, once
        return ctx.result(ID, SEVERITY, FIXABLE, passed=True, message="no meshes to check (see scene.has_meshes)")
    naming = ctx.conventions.naming
    stem = Path(ctx.source_path).stem
    problems, offenders, counts = [], [], {}

    def add(category, message, nodes):
        problems.append(message)
        offenders.extend(nodes)
        counts[category] = len(nodes) or 1

    # 1. Plain name rules.
    if not naming.check("group_node", stem).ok:
        add("file", "file name %r doesn't follow the convention (e.g. %r)"
            % (stem, _example(naming, "group_node")), [ctx.source_path])

    bad_meshes = [m for m in ctx.meshes if not naming.check("mesh_node", short_name(m)).ok]
    if bad_meshes:
        add("mesh_names", "%d mesh name(s) break the convention, e.g. %r (expected e.g. %r)"
            % (len(bad_meshes), short_name(bad_meshes[0]), _example(naming, "mesh_node")), bad_meshes)

    groups = [n for n in hierarchy(ctx) if n not in ctx.meshes]
    bad_groups = [g for g in groups if not naming.check("group_node", short_name(g)).ok]
    if bad_groups:
        add("group_names", "%d group name(s) break the convention, e.g. %r (expected e.g. %r)"
            % (len(bad_groups), short_name(bad_groups[0]), _example(naming, "group_node")), bad_groups)

    mats = sorted({mat for m in ctx.meshes for mat in materials(m)})
    bad_mats = [m for m in mats if not naming.check("material", m).ok]
    if bad_mats:
        add("material_names", "%d material name(s) break the convention: %s (expected e.g. %r)"
            % (len(bad_mats), ", ".join(bad_mats), _example(naming, "material")), bad_mats)

    # 2. Hierarchy rules.
    asset_roots = roots(ctx)
    if len(asset_roots) > 1:
        add("roots", "%d root objects hold meshes, expected one root named %r"
            % (len(asset_roots), stem), asset_roots)
    elif short_name(asset_roots[0]) != stem and asset_roots[0] not in bad_meshes + bad_groups:
        add("root_name", "root %r must be named like the file, %r" % (short_name(asset_roots[0]), stem),
            asset_roots)

    extra = [r for r in cmds.ls(assemblies=True, long=True)
             if r not in asset_roots and cmds.nodeType(r) != "joint" and not _is_startup_camera(r)]
    if extra:
        add("extra_roots", "%d extra root object(s) (exported as empty GameObjects): %s"
            % (len(extra), ", ".join(short_name(e) for e in extra)), extra)

    good = [m for m in ctx.meshes if m not in bad_meshes]
    wrong_base = [m for m in good if naming.strip_lod(short_name(m)) != stem]
    if wrong_base:
        add("mesh_base", "%d mesh(es) not named after the file: %r should be %r or %r"
            % (len(wrong_base), short_name(wrong_base[0]), stem, stem + "_LOD0"), wrong_base)

    with_lod = [m for m in good if naming.lod_level(short_name(m)) is not None]
    if with_lod and len(with_lod) != len(good):
        without = [m for m in good if m not in with_lod]
        add("lod_suffix", "_LODn suffix on %d mesh(es) but not on %s: use it on all or none"
            % (len(with_lod), ", ".join(short_name(m) for m in without)), without)

    allowed = ctx.asset_class.mesh_types
    wrong_prefix = [m for m in good if short_name(m).split("_")[0] not in allowed]
    if wrong_prefix:
        add("prefix", "%r uses prefix %s_ but %s allows %s (SM = static, SK = skinned)"
            % (short_name(wrong_prefix[0]), short_name(wrong_prefix[0]).split("_")[0],
               ctx.asset_class.name, ", ".join(t + "_" for t in allowed)), wrong_prefix)

    passed = not problems
    message = "names and hierarchy follow the convention" if passed else "; ".join(problems)
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"file_stem": stem, "materials": mats, "problems": counts},
                      offenders=offenders)
