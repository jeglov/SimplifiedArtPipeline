"""kit.images: every image is a readable PNG, has alpha where it must, and isn't blown up.

* Alpha: an image layer with fit "contain" (hero, logo) sits on top of the
  background, so an opaque file shows as a rectangle around the hero.
* Upscale: the largest factor the image is drawn at, over every placement
  and the layer's largest animated scale. Above `checks.max_upscale` the
  layer is visibly soft (a warning: a soft background can be a choice, a
  soft logo never is, so the message names the layer).
"""
from __future__ import annotations

from banners.core.checks.context import plural
from validate.core.image import ImageError, read_info
from validate.core.result import CheckResult, Severity

ID = "kit.images"
SEVERITY = Severity.WARNING
FIXABLE = False
DESCRIPTION = "Kit images are readable PNGs, with alpha on contain layers, not upscaled past the limit."


def fitted_scale(fit: str, box_w: float, box_h: float, img_w: int, img_h: int) -> float:
    """Scale of the image drawn in a box: cover fills (crops), contain fits (letterboxes)."""
    sx, sy = box_w / img_w, box_h / img_h
    return max(sx, sy) if fit == "cover" else min(sx, sy)


def run(ctx) -> CheckResult:
    limit = ctx.rules.checks.max_upscale
    unreadable, opaque, soft, worst = [], [], [], {}
    for layer in ctx.template.layers:
        if layer.kind != "image" or layer.image not in ctx.kit.images:
            continue
        img = ctx.kit.images[layer.image]
        if not img.path.is_file():
            unreadable.append("image.%s: %s missing" % (layer.image, img.path.name))
            continue
        try:
            info = read_info(img.path)
        except ImageError as exc:
            # ImageError messages name the file, not its folder: no machine path in the report.
            unreadable.append("image.%s: %s" % (layer.image, exc))
            continue
        if info.format != "png":
            unreadable.append("image.%s: %s is not a PNG" % (layer.image, img.path.name))
            continue
        if layer.fit == "contain" and not info.has_alpha:
            opaque.append("image.%s: no alpha, drawn as a rectangle in layer %s" % (layer.image, layer.id))
        peak = max([k.value for k in layer.tracks.get("scale", ())] + [1.0])
        for pid in ctx.placements:
            p = ctx.rules.placement(pid)
            up = fitted_scale(layer.fit, layer.box[2] * p.width, layer.box[3] * p.height,
                              info.width, info.height) * peak
            worst[layer.id] = max(worst.get(layer.id, 0.0), round(up, 3))
            if up > limit:
                soft.append("layer %s: %s (%dx%d) drawn at %.2fx in %s" % (layer.id, img.path.name, info.width,
                                                                          info.height, up, pid))
    problems = unreadable + opaque + soft
    metrics = {"upscale": worst, "max_upscale": limit}
    if problems:
        return CheckResult(ID, False, SEVERITY, "%s: %s" % (plural(len(problems), "image problem"), problems[0]),
                           FIXABLE, metrics, offenders=problems)
    return CheckResult(ID, True, SEVERITY, "images fit, largest upscale %.2fx" % max(worst.values() or [0.0]),
                       FIXABLE, metrics)
