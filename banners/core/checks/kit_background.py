"""kit.background: the hero stands out from the background around it (figure-ground). A warning.

Measured before rendering (banners/core/background.py), where the template
puts the hero: the share of the background ring around the hero's box with
the hero's own hue. Calibrated on the two Spotcap backgrounds: the first
(red mushrooms everywhere behind a red mushroom hero, rejected by eye in
step 4.4) has 37 % of the ring at the hero's hue, the second 0.1 %. Above
`checks.hero_clash_max` the hero doesn't read. `artpipe banners art
background --repair` retries the seed on this check.

Clutter behind the text is recorded as a metric, not judged: both Spotcap
backgrounds measure the same (~7.9), so there is no evidence for a
threshold yet (the first one's problem was hue, not detail).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from banners.core import background as bgm
from validate.core.image import ImageError, decode_png
from validate.core.result import CheckResult, Severity

ID = "kit.background"
SEVERITY = Severity.WARNING
FIXABLE = False
DESCRIPTION = "Under hero_clash_max of the ring around the hero has the hero's hue; clutter behind text recorded."


def _px(layer, w, h) -> Tuple[float, float, float, float]:
    return layer.box[0] * w, layer.box[1] * h, (layer.box[0] + layer.box[2]) * w, (layer.box[1] + layer.box[3]) * h


def measure(template, background, hero, placement) -> Dict[str, Any]:
    """{hero_hue, same_hue_share, clutter} for one placement (images as decoded Pixels)."""
    w, h = placement.width, placement.height
    bg_layer = next((layer for layer in template.layers if layer.image == "background"), None)
    scale = bg_layer.tracks["scale"][0].value if bg_layer and "scale" in bg_layer.tracks else 1.0
    placed = bgm.Placed(background, w, h, scale)
    hue = bgm.dominant_hue(hero)
    hero_layer = next((layer for layer in template.layers if layer.image == "hero"), None)
    texts = [_px(layer, w, h) for layer in template.layers if layer.kind == "text"]
    out = {"hero_hue": hue, "clutter": bgm.clutter(placed, texts)}
    if hue is not None and hero_layer is not None:
        out.update(bgm.clash(placed, _px(hero_layer, w, h), hue))
    return out


def run(ctx) -> CheckResult:
    limit = ctx.rules.checks.hero_clash_max
    images = ctx.kit.images
    if "background" not in images or "hero" not in images:
        return CheckResult(ID, True, SEVERITY, "no background or hero slot: nothing to compare", FIXABLE, {})
    try:
        bg, hero = decode_png(images["background"].path), decode_png(images["hero"].path)
    except (ImageError, OSError) as exc:
        return CheckResult(ID, False, SEVERITY, "can't read the art: %s" % exc, FIXABLE, {}, offenders=["art"])
    metrics: Dict[str, Any] = {"max_same_hue_share": limit}
    offenders: List[str] = []
    for pid in ctx.placements:
        m = measure(ctx.template, bg, hero, ctx.rules.placement(pid))
        metrics[pid] = m
        if m.get("same_hue_share", 0.0) > limit:
            offenders.append("%s: %.0f %% of the background around the hero has its hue (%.0f deg; max %.0f %%)"
                             % (pid, 100 * m["same_hue_share"], m["hero_hue"], 100 * limit))
    if offenders:
        return CheckResult(ID, False, SEVERITY, "the hero blends into the background: %s" % offenders[0], FIXABLE,
                           metrics, offenders=offenders)
    shares = [metrics[p].get("same_hue_share", 0.0) for p in ctx.placements]
    return CheckResult(ID, True, SEVERITY, "hero stands out (%.1f %% same hue around it)" % (100 * max(shares or [0])),
                       FIXABLE, metrics)
