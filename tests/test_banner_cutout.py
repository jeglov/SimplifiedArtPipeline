"""banners.core.cutout: hero art from a concept image + mask. Pure Python, <1 s."""
from __future__ import annotations

import pytest

from banners.core import cutout as cut
from validate.core.image import Pixels

BG = (228, 228, 228)
RED = (220, 40, 30)


def scene(w=20, h=20, obj=(5, 5, 15, 15), edge_mask=128):
    """Grey backdrop, a red square, and a 1-px edge ring that is a 50/50 mix (mask = edge_mask)."""
    img, mask = bytearray(), bytearray()
    x0, y0, x1, y1 = obj
    for y in range(h):
        for x in range(w):
            inside = x0 <= x < x1 and y0 <= y < y1
            ring = (x0 - 1 <= x <= x1 and y0 - 1 <= y <= y1) and not inside
            if inside:
                img += bytes(RED)
                mask += b"\xff"
            elif ring:
                img += bytes(round(0.5 * r + 0.5 * b) for r, b in zip(RED, BG))
                mask += bytes([edge_mask])
            else:
                img += bytes(BG)
                mask += b"\x00"
    return Pixels(w, h, 3, bytes(img)), Pixels(w, h, 1, bytes(mask))


def px(w, data, x, y):
    i = (y * w + x) * 4
    return tuple(data[i:i + 4])


def test_backdrop_is_the_border_median():
    img, _ = scene()
    assert cut.backdrop(img) == BG


def test_crop_to_the_mask_plus_margin():
    img, mask = scene()
    w, h, _ = cut.cutout(img, mask, lo=16, hi=240, margin=0.0)
    assert (w, h) == (12, 12)                    # the object plus its 1-px soft edge ring
    w, h, _ = cut.cutout(img, mask, margin=0.5)
    assert (w, h) == (20, 20)                    # margin clamped at the image border


def test_edge_pixels_lose_the_grey_halo():
    # The ring is 50 % red + 50 % grey with mask 128. Alpha from the 16..240
    # ramp is (128 - 16) / 224 = 0.5, so solving I = a*C + (1-a)*B gives RED back.
    img, mask = scene()
    w, h, data = cut.cutout(img, mask, margin=0.0)
    r, g, b, a = px(w, data, 0, 5)
    assert a == 128 and (r, g, b) == pytest.approx(RED, abs=2)
    assert px(w, data, 5, 5) == RED + (255,)


@pytest.mark.parametrize("m, alpha", [(16, 0), (17, 1), (240, 255), (239, 254), (0, 0), (255, 255)])
def test_alpha_ramp_boundaries(m, alpha):
    img, mask = scene(edge_mask=m)
    w, h, data = cut.cutout(img, mask, margin=0.0)
    if alpha:
        assert px(w, data, 0, 5)[3] == alpha
    else:  # at or under lo the ring is not part of the object: the crop is the inner square only
        assert (w, h) == (10, 10)


def test_empty_mask_and_bad_input():
    img, mask = scene()
    with pytest.raises(ValueError, match="empty"):
        cut.cutout(img, Pixels(20, 20, 1, bytes(400)))
    with pytest.raises(ValueError, match="differ"):
        cut.cutout(img, Pixels(10, 10, 1, bytes(100)))
    with pytest.raises(ValueError, match="lo < hi"):
        cut.cutout(img, mask, lo=200, hi=100)


def test_crop_to_alpha():
    data = bytearray(10 * 10 * 4)
    for x, y in ((3, 4), (6, 7)):
        data[(y * 10 + x) * 4 + 3] = 255
    w, h, out = cut.crop_to_alpha(Pixels(10, 10, 4, bytes(data)), margin_px=1)
    assert (w, h) == (6, 6) and len(out) == 6 * 6 * 4
    assert out[((4 - 3) * 6 + (3 - 2)) * 4 + 3] == 255   # (3, 4) lands at (1, 1)
    with pytest.raises(ValueError, match="fully transparent"):
        cut.crop_to_alpha(Pixels(2, 2, 4, bytes(16)))
