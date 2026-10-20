"""Record the golden banner for tests/test_banner_golden.py.

    artpipe py tests/fixtures/banners/golden_record.py

Renders the shipped kit (spotcap_grove) in the 1 s test template and writes
tests/fixtures/banners/golden/spotcap_grove.short_endcard.json: the MP4's
sha256, the layout engine's text measure and font record, and a coarse
colour grid of every decoded key frame. Re-record after a deliberate change
to the renderer, the template, the kit or a pinned tool, and say why in the
commit.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from banners import build, tools  # noqa: E402
from banners.core import kit as km  # noqa: E402
from banners.core import rules as br  # noqa: E402
from banners.core import template as tm  # noqa: E402
from validate.core.image import decode_png  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "short_endcard.yaml"
GOLDEN = Path(__file__).resolve().parent / "golden" / "spotcap_grove.short_endcard.json"
GRID = (12, 21)


def grid(path):
    """Mean RGB of each cell of a GRID[0] x GRID[1] grid (sampled every 4 px inside the cell)."""
    px = decode_png(path)
    gw, gh = GRID
    out = []
    for gy in range(gh):
        for gx in range(gw):
            x0, x1 = gx * px.width // gw, (gx + 1) * px.width // gw
            y0, y1 = gy * px.height // gh, (gy + 1) * px.height // gh
            samples = [px.rgb(x, y) for y in range(y0, y1, 4) for x in range(x0, x1, 4)]
            out.append([round(sum(s[c] for s in samples) / len(samples)) for c in range(3)])
    return out


def snapshot(banner):
    return {"mp4_sha256": tools.sha256_file(banner.mp4),
            "measure": banner.layout["ready"]["measure"],
            "fonts": banner.layout["fonts"],
            "key_frames": banner.layout["page"]["key_frames"],
            "grids": {str(n): grid(p) for n, p in sorted(banner.key_frames.items())}}


def render(out_dir):
    return build.render_banner(br.load(), tm.load(str(TEMPLATE)), km.load("spotcap_grove"), km.load_fonts(),
                               "ua_video_9x16", "en", Path(out_dir))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        snap = snapshot(render(tmp))
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    with GOLDEN.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(snap, indent=1, sort_keys=True) + "\n")
    print("wrote", GOLDEN.relative_to(ROOT), "mp4", snap["mp4_sha256"][:12])


if __name__ == "__main__":
    main()
