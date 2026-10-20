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
