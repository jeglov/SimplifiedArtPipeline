"""Is a concept image usable for image-to-3D? Checks on the pixels, before GPU time is spent.

Image-to-3D models want one whole object, centred, on a plain backdrop. A
concept image that breaks that costs a 3D run (minutes on 8 GB) and gives a
mesh with a floor, a second object or a cut-off side. These checks catch it
from the image alone:

  concept.background     the border ring is one plain colour
  concept.coverage       the object covers a sensible share of the frame
  concept.margin         the object doesn't touch or leave the frame
  concept.single_object  one connected object, not a set or a scene

The background check reads the image; the object checks read the BiRefNet
foreground mask, the same mask image-to-3D gets. By colour alone a soft
contact shadow counts as object (probed on the first generated coin: it set
the margin), in the mask it doesn't. Both are sampled on a grid
(conventions/generation.yaml `concept.grid`): ~16k lookups for 1024 px.
Same pixels in, same results out. Pure Python 3.9.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from gen.core.rules import ConceptRules
from validate.core.image import Pixels, sample_grid
from validate.core.result import CheckResult, Severity

RGB = Tuple[int, int, int]


def _median(values: Sequence[int]) -> int:
    s = sorted(values)
    return s[len(s) // 2]


def border_ring(grid: List[List[RGB]]) -> List[RGB]:
    n = len(grid)
    ring = [grid[0][x] for x in range(n)] + [grid[n - 1][x] for x in range(n)]
    ring += [grid[y][0] for y in range(1, n - 1)] + [grid[y][n - 1] for y in range(1, n - 1)]
    return ring


def _close(a: RGB, b: RGB, tol: int) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) <= tol


def components(mask: List[List[bool]]) -> List[int]:
    """Sizes of the 4-connected True regions, largest first."""
    n_y, n_x = len(mask), len(mask[0])
    seen = [[False] * n_x for _ in range(n_y)]
    sizes = []
    for y0 in range(n_y):
        for x0 in range(n_x):
            if not mask[y0][x0] or seen[y0][x0]:
                continue
            seen[y0][x0] = True
            stack, size = [(x0, y0)], 0
            while stack:
                x, y = stack.pop()
                size += 1
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < n_x and 0 <= ny < n_y and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            sizes.append(size)
    return sorted(sizes, reverse=True)


def check(image: Pixels, mask: Pixels, rules: ConceptRules, shown_as: str) -> List[CheckResult]:
    """`mask`: the foreground mask image-to-3D will use (BiRefNet), white = object."""
    if (mask.width, mask.height) != (image.width, image.height):
        raise ValueError("mask is %dx%d, image %dx%d" % (mask.width, mask.height, image.width, image.height))
    n = rules.grid
    grid = sample_grid(image, n)
    ring = border_ring(grid)
    bg = (_median([c[0] for c in ring]), _median([c[1] for c in ring]), _median([c[2] for c in ring]))
    tol = rules.background_tolerance
    uniform = sum(1 for c in ring if _close(c, bg, tol)) / float(len(ring))
    mask_grid = [[c[0] >= rules.mask_threshold for c in row] for row in sample_grid(mask, n)]
    object_cells = sum(sum(row) for row in mask_grid)
    coverage = object_cells / float(n * n)
    results = []

    def add(cid, passed, message, **metrics):
        results.append(CheckResult(cid, passed, Severity.ERROR, message, False, metrics=metrics,
                                   offenders=() if passed else (shown_as,)))

    ok = uniform >= rules.background_uniformity_min
    add("concept.background", ok,
        "Plain background rgb%s (%.0f%% of the border)." % (bg, 100 * uniform) if ok else
        "The border is not one plain colour (%.0f%% matches rgb%s, need %.0f%%): a scene, a "
        "gradient or a cropped object. Image-to-3D would model it." % (
            100 * uniform, bg, 100 * rules.background_uniformity_min),
        background_rgb=list(bg), uniformity=uniform)

    ok = rules.coverage_min <= coverage <= rules.coverage_max
    add("concept.coverage", ok,
        "The object covers %.0f%% of the image." % (100 * coverage) if ok else
        "The object covers %.0f%% of the image, allowed %.0f%%-%.0f%%: %s." % (
            100 * coverage, 100 * rules.coverage_min, 100 * rules.coverage_max,
            "too small for detail" if coverage < rules.coverage_min else "too big, likely cropped"),
        coverage=coverage)

    if object_cells:
        xs = [x for row in mask_grid for x, v in enumerate(row) if v]
        ys = [y for y, row in enumerate(mask_grid) for v in row if v]
        margin = min(min(xs), min(ys), n - 1 - max(xs), n - 1 - max(ys)) / float(n)
    else:
        margin = 0.0
    ok = object_cells > 0 and margin >= rules.margin_min
    add("concept.margin", ok,
        "%.0f%% free space to the nearest edge." % (100 * margin) if ok else
        "No object found." if not object_cells else
        "The object reaches the edge (%.1f%% margin, need %.0f%%): part of it may be cut off." % (
            100 * margin, 100 * rules.margin_min),
        margin=margin)

    sizes = [s for s in components(mask_grid) if s >= rules.speck_cells]
    share = sizes[0] / float(sum(sizes)) if sizes else 0.0
    ok = bool(sizes) and share >= rules.single_object_min
    add("concept.single_object", ok,
        ("One object." if len(sizes) == 1 else
         "One main object (%d regions, the largest holds %.0f%%)." % (len(sizes), 100 * share)) if ok else
        "No object found." if not sizes else
        "%d separate objects: the largest holds only %.0f%% (need %.0f%%). Image-to-3D would fuse "
        "them into one mesh." % (len(sizes), 100 * share, 100 * rules.single_object_min),
        regions=len(sizes), largest_share=share)
    return results
