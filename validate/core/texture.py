"""Texture source checks in Python, before a texture reaches Unity (backlog #40).

The same check ids, severities, metrics and rules as the Unity package's
TextureRulesUnity.SourceChecks, read from the same conventions, so a texture
judged here gets the verdict Unity would give it. Generated textures are
checked at generation time instead of failing at import.

Not here: texture.platform_format, which only exists after Unity compressed
the texture. Pure Python 3.9.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

from validate.core.conventions import Conventions
from validate.core.image import ImageError, read_info
from validate.core.result import CheckResult, Severity


def map_type(name: str, c: Conventions) -> Optional[str]:
    """"T_Crate_A_N.png" -> "N" (the suffix before the extension), if it is a known type."""
    stem = name.rsplit(".", 1)[0]
    suffix = stem.rsplit("_", 1)[-1] if "_" in stem else ""
    return suffix if suffix in {t.suffix for t in c.textures.types} else None


def _is_pot(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def max_size(c: Conventions, asset_class: Optional[str]) -> int:
    if asset_class is None:
        return c.textures.default_max_size
    return c.asset_class(asset_class).texture_max_size


def source_checks(path: Union[str, Path], c: Conventions, asset_class: Optional[str],
                  shown_as: Optional[str] = None) -> List[CheckResult]:
    """Check one texture file. `shown_as` is the path written in offenders.

    Raises ImageError when the file is a PNG/TGA that can't be read: that is
    a tool error, not a verdict on the texture.
    """
    p = Path(path)
    where = shown_as or p.name
    rules = c.textures
    name = p.name
    results = []

    def add(cid, passed, severity, message, **metrics):
        results.append(CheckResult(cid, passed, severity, message, False, metrics=metrics,
                                   offenders=() if passed else (where,)))

    named = c.naming.check("texture_file", name).ok
    mtype = map_type(name, c)
    suffixes = "|".join(sorted(t.suffix for t in rules.types))
    add("texture.name", named and mtype is not None, Severity.ERROR,
        "Map type %s." % mtype if named and mtype else
        "%r doesn't follow T_<Name>[_Variant]_<%s>.<%s>, e.g. %s. The suffix sets color space, "
        "alpha and compression." % (name, suffixes, "|".join(rules.source_formats),
                                     c.naming.rule("texture_file").examples[0]))

    ext = p.suffix.lstrip(".").lower()
    format_ok = ext in rules.source_formats
    add("texture.source_format", format_ok, Severity.ERROR,
        "Source is %s." % ext if format_ok else
        "Source is .%s; use %s (lossless, flattened)." % (ext, " or ".join(rules.source_formats)),
        format=ext)
    if not format_ok:
        return results  # nothing below can be read from a format we don't accept

    info = read_info(p)
    w, h = info.width, info.height
    pot = not rules.power_of_two or (_is_pot(w) and _is_pot(h))
    add("texture.power_of_two", pot, Severity.ERROR,
        "%dx%d." % (w, h) if pot else
        "%dx%d is not a power of two: it gets no full mip chain and may not compress (probed in "
        "Unity: an NPOT texture shipped uncompressed RGBA32)." % (w, h), width=w, height=h)

    big_enough = w >= rules.min_size and h >= rules.min_size
    add("texture.min_size", big_enough, Severity.ERROR,
        "At least %d px." % rules.min_size if big_enough else
        "%dx%d is below the %d px minimum." % (w, h, rules.min_size), min_size=rules.min_size)

    square = not rules.square_required or w == h
    add("texture.square", square, Severity.ERROR,
        "Shape allowed." if square else "%dx%d must be square." % (w, h))

    limit = max_size(c, asset_class)
    fits = w <= limit and h <= limit
    add("texture.source_size", fits, Severity.WARNING,
        "Fits the %d px limit." % limit if fits else
        "%dx%d source ships at %d px max: author at the shipped size (smaller repo, no surprise "
        "downscale)." % (w, h, limit), max_size=limit)

    if mtype is not None:
        rule = next(t.alpha for t in rules.types if t.suffix == mtype)
        has = info.has_alpha
        ok = rule == "optional" or (rule == "required") == has
        channels = next(t.channels for t in rules.types if t.suffix == mtype)
        add("texture.alpha", ok, Severity.ERROR,
            "Alpha %s, source %s alpha." % (rule, "has" if has else "has no") if ok else
            "_%s needs an alpha channel (%s)." % (mtype, ", ".join("%s=%s" % kv for kv in channels))
            if rule == "required" else
            "_%s must not have an alpha channel: it costs memory and some compressors treat it as "
            "data. Export RGB only." % mtype,
            has_alpha=has, alpha_rule=rule)
    return results


__all__ = ["ImageError", "map_type", "max_size", "source_checks"]
