"""kit.licences: no asset without a licence record we are allowed to ship.

Fonts: an allowed licence (conventions/banners.yaml `licences.allowed`) and
the licence text next to the file (the OFL requires it to travel with the
font). Images: an allowed licence; a generated image ("generated") must have
a provenance record (kit.files proves it is this file's) whose every model
has a licence that lets us ship its outputs (`licences.generator_models`).
A logo or a hero with no record is the kind of thing that gets a creative
pulled from a network after launch.
"""
from __future__ import annotations

from banners.core.checks.context import plural
from validate.core.result import CheckResult, Severity

ID = "kit.licences"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every font and image has an allowed licence record (and provenance if generated)."
GENERATED = "generated"


def _provenance_problem(img, rules) -> str:
    record = img.record
    if record is None:
        return "image.%s: licence 'generated' needs a readable %s" % (img.slot, img.provenance.name)
    models = record.get("models") or []
    if not models:
        return "image.%s: provenance names no model" % img.slot
    bad = sorted("%s (%s)" % (m.get("id"), m.get("licence")) for m in models
                 if m.get("licence") not in rules.generator_models)
    if bad:
        return "image.%s: generated with models whose outputs we may not ship: %s" % (img.slot, ", ".join(bad))
    return ""


def run(ctx) -> CheckResult:
    rules = ctx.rules.licences
    problems, checked = [], 0
    for fid in sorted(set(ctx.kit.fonts.values())):
        font = ctx.fonts.get(fid)
        if font is None:
            continue  # kit.slots reports it
        checked += 1
        if font.licence not in rules.allowed:
            problems.append("font.%s: licence %r is not allowed" % (fid, font.licence))
        elif not font.licence_file.is_file():
            problems.append("font.%s: licence text %s missing" % (fid, font.licence_file.name))
    for slot, img in sorted(ctx.kit.images.items()):
        checked += 1
        if img.licence not in rules.allowed:
            problems.append("image.%s: licence %r is not allowed" % (slot, img.licence))
        elif img.licence == GENERATED:
            problem = _provenance_problem(img, rules)
            if problem:
                problems.append(problem)
    if problems:
        return CheckResult(ID, False, SEVERITY, "%s: %s" % (plural(len(problems), "licence problem"), problems[0]),
                           FIXABLE, {"assets": checked, "problems": len(problems)}, offenders=problems)
    return CheckResult(ID, True, SEVERITY, "%d assets, every one with an allowed licence" % checked, FIXABLE,
                       {"assets": checked, "problems": 0})
