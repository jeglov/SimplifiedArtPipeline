# UA creatives (Phase 4)

One template, filled by a per-game kit, rendered to every placement and
checked like any other asset: `{id, passed, severity, metrics, message,
fixable}` per check, exit 0 / 1 / 2, deterministic outputs under `out/`.

Scope agreed on 2026-10-20 (see `CLAUDE.md`): UA video creatives, one
placement (9:16, 1080x1920, H.264 MP4, silent), one fictional game in `en`
for the demo, the batch built for N games x placements x locales.

## Specs (`conventions/banners.yaml`)

A creative is uploaded once and shown by several networks, so each rule is
the strictest of Meta (Reels), TikTok (In-Feed), Unity Ads and AppLovin.
Read on 2026-10-20 from each network's own spec page (URLs in the YAML):

| Rule | Value | Strictest source | Others |
|---|---|---|---|
| Size | 1080x1920 (9:16) | all: 9:16 | Meta recommends 1440x2560; TikTok min 540x960; AppLovin min 270x480 |
| Codec / container | H.264 in MP4 | Unity: "H.264-encoded MP4 format" | Meta: H.264; AppLovin: MP4/MOV/M4V |
| Frame rate | constant, 30 fps | Meta: "fixed frame rate" | no network names a number: 30 is a house rule |
| Duration | 6-60 s | Unity, AppLovin: 60 s max | Meta 15 min, TikTok 10 min; no published minimum (6 s is a house rule) |
| File size | <= 100 MB, warn > 10 MB | Unity: 100 MB max, 10 MB recommended | TikTok 500 MB, AppLovin 1 GB, Meta 4 GB |
| Bitrate | >= 516 kbps | TikTok | |
| Safe zone | 14 % top, 35 % bottom, 6 % sides | Meta (the only one stated in text) | TikTok and Unity publish overlay templates as images only |
| End card | required | Unity: "all video ads must also include an end card" | |

Two of these matter more than they look:

* **The bitrate floor bites a template video.** A mostly static banner
  compresses very well: an encoder in quality mode (CRF) can land under
  516 kbps and TikTok rejects the upload. The encoder settings must keep a
  floor, and a check measures the real bitrate of the file.
* **The safe zone is where the platform's own UI goes** (caption, profile,
  like/share column, CTA bar). In pixels: x 65-1015, y 269-1248. Text, the
  logo and the CTA must stay inside it; the background and the hero may
  bleed to the edges.

## Pinned tools (`artpipe banners setup`)

| Tool | Version | Pinned by | Licence |
|---|---|---|---|
| ffmpeg + ffprobe | 9.0.2 essentials (gyan.dev, GitHub mirror) | archive sha256 (GitHub's published digest) + exe sha256 | GPL-3.0-or-later (links libx264). We only run it; our videos aren't covered |
| Chrome headless shell | 153.0.8010.12 (Chrome for Testing) | archive sha256 (first download, 2026-10-20: CfT publishes no hashes) + exe sha256 | BSD-3-Clause + third-party notices |
| Playwright | 1.63.0 | exact version in `package.json`, integrity hashes in `package-lock.json` | Apache-2.0 |

Installed to `%LOCALAPPDATA%\artpipe\<tool>-<version>\` (or
`ARTPIPE_TOOLS`), never on PATH: the pipeline calls them by absolute path
after checking their hashes. Playwright 1.63.0 was chosen over 1.64.1 (6 days
old at the time) for a release that had been out for a while.

Probed while installing:

* **Playwright's own browser download timed out** (`install --only-shell
  chromium`: "Request ... timed out after 30000ms", also with
  `PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=180000`), while curl fetched the
  same URL at once (a 307 to storage.googleapis.com). Rather than fight the
  downloader, setup fetches the exact build Playwright 1.63.0 expects
  (`install --dry-run` names Chrome Headless Shell 153.0.8010.12) with
  urllib, checks it, and Playwright launches it by `executablePath`. Better
  anyway: the pipeline owns the bytes it pins.
* `npm ci --ignore-scripts`: Playwright's postinstall would otherwise try to
  download browsers.
* ffmpeg 9.0.2 essentials has `libx264`; Playwright's own ffmpeg (in
  `ms-playwright\ffmpeg-1011`) only encodes VP8 and PNG, and is not used.

## Renderer (`banners/render.py`, `banners/render/render.mjs`)

Python computes the state of every layer at every frame
(`banners/core/timeline.py`: keyframes, easings) and writes the page; the
page has no CSS animations and no timers. For frame n, `render.mjs` calls
`window.artpipe.apply(n)` (the page sets the numbers and returns its layout),
then takes a PNG screenshot. Time never runs, so a slow machine produces the
same frames as a fast one and no frame is dropped. That's the difference with
screen recording (Unity Recorder in real time, Playwright's `recordVideo`):
there a busy machine drops or duplicates frames.

The page is served from `http://banner.invalid/` through a request route:
every request is answered from the job's file list or refused, and a refused
request fails the render (exit 2). A template can't load a font or image
from the network, and a missing file can't silently become a fallback.

Chromium runs with software raster, no LCD subpixel text, no font hinting,
sRGB output and device scale 1 (`ARGS` in `render.mjs`).

Probed on 2026-10-20 (`tests/test_banner_render.py` keeps each one true):

| Probe | Result |
|---|---|
| Same page, 30 frames, two separate browser launches | **byte-identical PNGs** and identical layout JSON |
| Speed | ~0.35 s per 1080x1920 frame (screenshot + PNG encode): an 8 s, 240-frame banner in ~85 s |
| `document.fonts.check('48px "NoSuchFont"')` | **true**: the API answers "nothing to load", not "the font exists". Useless as a check |
| A family that isn't loaded | drawn in Times New Roman, no error, no console message |
| Fredoka with Cyrillic ("Tap Привет") | 4 glyphs in Fredoka, **6 in Times New Roman**, silently: Fredoka has no Cyrillic |
| CDP `CSS.getPlatformFontsForNode` | names the fonts that really drew a node and how many glyphs each: the font-fallback check reads this |
| x264, same PNGs, `-threads 1` twice / `-threads 0` twice | identical within each setting, **different between them**: the thread count changes the bytes, so it is pinned (4) |
| x264 CRF 20 on a static frame | **150 kbps**: under TikTok's 516 kbps floor |
| x264 CBR 5 Mbps (`nal-hrd=cbr`) on the same frame | 4.8 Mbps, byte-identical across runs |

So the outputs are byte-identical on this machine. On another machine they
should be too (same pinned exes, software raster, fixed thread count), but
that isn't proven yet (backlog #61). The checks never rely on it: they read
the layout record and **decoded** frames, not file hashes. Reuse of earlier
outputs is keyed by the inputs' hash, not by the output's.

## Template, kits and licences (step 4.3)

**The template** (`banners/templates/hero_endcard.yaml`, read by
`banners/core/template.py`) is layers (image, text, button) with boxes as
shares of the frame, keyframe tracks, and the slots a kit must fill. It
never names a colour value, a font file or a string. The strict reader
refuses an undeclared slot, a declared slot no layer uses (it caught one in
the first draft: `colour.background`), keys out of order, a text, logo or
CTA box outside the frame, an unknown easing.

**A kit** (`banners/kits/<game>/kit.yaml`, `banners/core/kit.py`) is one
game: palette, which palette colour fills each colour slot, font ids from
`banners/fonts/fonts.yaml`, images, strings per locale. A malformed kit is a
tool error (exit 2); whether its content is usable is judged by checks
(exit 1), so an agent gets a report it can act on:

| Check | Severity | Fails when |
|---|---|---|
| `kit.slots` | error | a template slot has no value (whitespace counts as empty), in any locale asked for; a colour slot names no palette entry; a font id isn't registered |
| `kit.files` | error | a file is missing, has no provenance record, or its sha256 differs from the record |
| `kit.licences` | error | a font or image licence isn't in `licences.allowed`; a font's licence text is missing; a generated image names a model whose licence isn't in `licences.generator_models` |
| `kit.colours` | error | a palette value isn't `#RRGGBB` (the contrast checks compute luminance from these) |
| `kit.images` | warning | not a PNG; an image on a `contain` layer has no alpha (drawn as a rectangle); an image is drawn above `checks.max_upscale` (1.5x) in any placement, including the layer's largest animated scale |

**Licences.** No asset without a record. Fonts: one record per file in
`fonts.yaml` (Fredoka and Noto Sans, OFL-1.1, from google/fonts at a pinned
commit, licence text next to each file). Images: every kit image has a
sidecar `<file>.provenance.json` (`conventions/banner_provenance.schema.json`)
with its sha256, how it was made, from what, and every model with its
licence. kit.yaml only names the file and the licence id, so remaking art
never means editing kit.yaml.

**Kit art** (`artpipe banners art`, `banners/art.py`), the AI part kept simple:

* `hero`: the cut-out of a passing Phase 3 concept with its BiRefNet mask,
  so the banner hero is the object the game's 3D asset was built from.
  Alpha goes through a 16-240 ramp, and edge pixels are decontaminated: an
  edge is I = a C + (1 - a) B, so solving for C with B = the backdrop colour
  (median of the image border) removes the grey halo the studio backdrop
  would leave on a green background. A boundary test caught an off-by-one:
  a mask value exactly at the ramp's low end has alpha 0 but was counted
  into the crop.
* `background`: one Z-Image-Turbo image per game on the pinned ComfyUI
  (the concept workflow at 864x1536, its mask ignored), cached in the kit.
  864 px is drawn at 1.40x in the 1080 px frame with the 1.12x push-in:
  under the 1.5x limit.
* `logo`: `logo.html` (a wordmark set in Fredoka; the OFL allows logos)
  rendered by the pinned Chromium with a transparent background and cropped
  to its alpha. Re-rendering gives the committed file byte for byte.
  **AI never makes text or logos**: diffusion models garble letters, and a
  generated logo has no trademark certainty.

Records never hold machine paths: a run outside the repo is recorded as
`<outside-repo>/<name>`; the schema refuses absolute paths (it caught this
on the first test run).

The demo game is **Spotcap Grove**, a made-up casual merge game.
