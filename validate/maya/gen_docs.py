"""Regenerate the tables in docs/conventions.md from the YAML and the registry.

    artpipe docs            rewrite the generated blocks in place
    artpipe docs --check    exit 1 if the doc is out of date (used by the tests)

Only the text between <!-- generated:NAME --> and <!-- /generated:NAME -->
is rewritten; everything else is hand-written prose. Numbers in a
conventions doc that are typed by hand drift from the real rules within
weeks; these can't.

Imports the check/fix modules but never starts Maya (they only call Maya
when they run), so this takes under a second.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from validate.core import conventions as conv
from validate.core.paths import REPO_ROOT

DOC = REPO_ROOT / "docs" / "conventions.md"
_BLOCK = re.compile(r"(<!-- generated:(\w+) -->\n)(.*?)(<!-- /generated:\2 -->)", re.DOTALL)


def _table(header: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _num(x: float) -> str:
    return ("%g" % x)


def _classes(c: conv.Conventions) -> str:
    rows = []
    for a in c.classes:
        overrides = ", ".join("`%s` → %s" % (k, v.value) for k, v in a.severity_overrides) or "—"
        rows.append([
            "`%s`" % a.name, a.description,
            ", ".join("`%s_`" % t for t in a.mesh_types),
            "{:,}".format(a.tris_max),
            str(a.lods.min) if a.lods.min == a.lods.max else "%d–%d" % (a.lods.min, a.lods.max),
            "%s–%s" % (_num(a.size_cm_min), _num(a.size_cm_max)),
            a.pivot.mode,
            str(a.max_materials),
            "yes" if a.uv.unit_square else "no (tiling)",
            str(a.texture_max_size),
            overrides,
        ])
    text = _table(["Class", "Use", "Prefix", "Max tris (LOD0)", "LOD levels", "Size (cm)", "Pivot",
                   "Max materials", "UV0 inside 0–1", "Max texture (px)", "Severity overrides"], rows)
    ratios = {(a.lods.max_ratio, a.lods.tolerance) for a in c.classes}
    if len(ratios) == 1:
        (r, tol), = ratios
        text += ("\nLOD budget (all classes): LOD*n* may have at most %s of LOD0's triangles "
                 "for n = 0..%d, plus %s%% tolerance.\n"
                 % (" / ".join("%g%%" % (x * 100) for x in r), len(r) - 1, _num(tol * 100)))
    pivots = {a.pivot.tolerance_cm for a in c.classes}
    if len(pivots) == 1:
        text += "Pivot tolerance (all classes): %s cm.\n" % _num(pivots.pop())
    return text


def _textures(c: conv.Conventions) -> str:
    t = c.textures
    rows = [["`_%s`" % x.suffix, x.description, x.color_space, x.alpha] for x in t.types]
    return (_table(["Suffix", "Map", "Color space", "Alpha"], rows) +
            "\nSource formats: %s. Power of two: %s. Square required: %s. Minimum size: %d px.\n"
            % (", ".join(t.source_formats), "yes" if t.power_of_two else "no",
               "yes" if t.square_required else "no", t.min_size))


def _checks(c: conv.Conventions) -> str:
    from validate.maya import registry
    # SEVERITY_DOC: optional wording for checks whose severity depends on the class.
    rows = [[str(i), "`%s`" % m.ID, m.DESCRIPTION, getattr(m, "SEVERITY_DOC", m.SEVERITY.value),
             "yes" if m.FIXABLE else "—"]
            for i, m in enumerate(registry.CHECKS, 1)]
    return _table(["#", "Check id", "What it enforces", "Default severity", "Auto-fix"], rows)


def _fixes(c: conv.Conventions) -> str:
    from validate.maya import registry
    rows = [[str(i), "`%s`" % m.ID, m.DESCRIPTION] for i, m in enumerate(registry.FIXES, 1)]
    return _table(["Order", "Fixes check", "What it changes"], rows)


SECTIONS: Dict[str, Callable[[conv.Conventions], str]] = {
    "classes": _classes, "textures": _textures, "checks": _checks, "fixes": _fixes,
}


def render(text: str, conventions: conv.Conventions) -> str:
    """Return `text` with every generated block rebuilt. Raises on unknown/missing blocks."""
    found = set()

    def replace(m: "re.Match[str]") -> str:
        name = m.group(2)
        if name not in SECTIONS:
            raise ValueError("unknown generated block %r in %s" % (name, DOC.name))
        found.add(name)
        return m.group(1) + SECTIONS[name](conventions) + m.group(4)

    out = _BLOCK.sub(replace, text)
    missing = sorted(set(SECTIONS) - found)
    if missing:
        raise ValueError("%s has no generated block(s) %s" % (DOC.name, missing))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="validate.maya.gen_docs", description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="exit 1 if docs/conventions.md is out of date")
    args = p.parse_args(argv)
    current = DOC.read_text(encoding="utf-8")
    fresh = render(current, conv.load())
    if args.check:
        if fresh != current:
            print("docs/conventions.md is out of date: run `artpipe docs`", file=sys.stderr)
            return 1
        print("docs/conventions.md is up to date")
        return 0
    with DOC.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(fresh)
    print("rewrote generated blocks in docs/conventions.md" if fresh != current else "already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
