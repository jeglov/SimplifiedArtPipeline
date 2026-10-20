"""conventions/banners.yaml as typed, immutable rules.

Same strict reader as validate.core.conventions: an unknown or mistyped key
fails at load time with its full path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from banners.core.video import EncodeRules
from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml, _Reader

FILE = "banners.yaml"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^\d+(\.\d+){2,3}$")
FFMPEG_EXES = ("ffmpeg", "ffprobe")
CHROMIUM_EXES = ("chrome-headless-shell",)
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
CONTAINERS = ("mp4",)
CODECS = ("h264",)
PIX_FMTS = ("yuv420p",)


@dataclass(frozen=True)
class ToolPin:
    """A downloaded tool: one zip, pinned by sha256, unpacked by `artpipe banners setup`."""
    id: str
    version: str
    archive_dir: str            # folder inside the zip that is extracted
    source: str
    archive_sha256: str
    licence: str
    exe_sha256: Dict[str, str]  # exe name without .exe -> sha256, checked before every use


@dataclass(frozen=True)
class PlaywrightPin:
    version: str
    licence: str


@dataclass(frozen=True)
class SafeZone:
    """Margins as a share of the frame: nothing readable may sit in them."""
    top: float
    bottom: float
    left: float
    right: float

    def box(self, width: int, height: int):
        """(x0, y0, x1, y1) in pixels, the inside of the margins."""
        return (round(self.left * width), round(self.top * height),
                width - round(self.right * width), height - round(self.bottom * height))


@dataclass(frozen=True)
class Placement:
    id: str
    description: str
    width: int
    height: int
    fps: int
    container: str
    codec: str
    pix_fmt: str
    audio: bool
    duration_min_s: float
    duration_max_s: float
    max_bytes: int
    target_bytes: int
    min_bitrate_kbps: int
    safe_zone: SafeZone


@dataclass(frozen=True)
class LicenceRules:
    allowed: Tuple[str, ...]              # licence ids a kit asset may carry
    generator_models: Tuple[str, ...]     # model licences whose outputs we may ship


@dataclass(frozen=True)
class CheckRules:
    """House rules for the kit and banner checks (no network publishes these)."""
    max_upscale: float
    visible_opacity: float
    safe_zone_tolerance_px: float
    min_text_share: float
    cta_min_height_share: float
    cta_min_text_share: float
    contrast_min: float
    contrast_percentile: float
    cta_text_contrast_min: float
    cta_fill_contrast_min: float
    end_card_min_s: float
    first_frame_min_std: float
    logo_aspect_tolerance: float
    batch_distinct_min: float
    hero_clash_max: float


REPAIR_ACTIONS = ("shrink_text", "with_text_fit", "recolour_text", "recolour_cta", "stop")


@dataclass(frozen=True)
class RepairRules:
    max_renders: int
    max_measure_passes: int
    fit_margin: float
    default: str
    actions: Dict[str, str]

    def action_for(self, check_id: str) -> str:
        return self.actions.get(check_id, self.default)


@dataclass(frozen=True)
class BannerRules:
    ffmpeg: ToolPin
    chromium: ToolPin
    playwright: PlaywrightPin
    encode: EncodeRules
    licences: LicenceRules
    checks: CheckRules
    repair: RepairRules
    placements: Dict[str, Placement]

    def placement(self, pid: str) -> Placement:
        if pid not in self.placements:
            raise ConventionError("unknown placement %r, expected one of %s" % (pid, sorted(self.placements)))
        return self.placements[pid]


def _positive(r: _Reader, key: str, typ=int):
    val = r.get(key, typ)
    if val <= 0:
        raise ConventionError("%s.%s: must be > 0, got %s" % (r.where, key, val))
    return val


def _share(r: _Reader, key: str) -> float:
    val = r.get(key, float)
    if not 0.0 <= val < 0.5:
        raise ConventionError("%s.%s: a margin must be in [0, 0.5), got %s" % (r.where, key, val))
    return val


def _choice(r: _Reader, key: str, options) -> str:
    val = r.get(key, str)
    if val not in options:
        raise ConventionError("%s.%s: expected one of %s, got %r" % (r.where, key, list(options), val))
    return val


def _sha(r: _Reader, key: str) -> str:
    val = r.get(key, str)
    if not _SHA256.match(val):
        raise ConventionError("%s.%s: expected a lowercase sha256" % (r.where, key))
    return val


def _tool(tid: str, r: _Reader, exes) -> ToolPin:
    version = r.get("version", str)
    if not _VERSION.match(version):
        raise ConventionError("%s.version: expected an exact dotted version, got %r" % (r.where, version))
    archive_dir = r.get("archive_dir", str)
    if archive_dir.startswith(("/", "\\")) or ".." in archive_dir.replace("\\", "/").split("/"):
        raise ConventionError("%s.archive_dir: must be a relative folder inside the archive" % r.where)
    source = r.get("source", str)
    if not source.startswith("https://"):
        raise ConventionError("%s.source: must be an https URL" % r.where)
    exe = r.sub("exe_sha256")
    hashes = {name: _sha(exe, name) for name in exes}
    exe.done()
    pin = ToolPin(tid, version, archive_dir, source, _sha(r, "archive_sha256"), r.get("licence", str), hashes)
    r.done()
    return pin


def _playwright(r: _Reader) -> PlaywrightPin:
    version = r.get("version", str)
    if not _VERSION.match(version):
        raise ConventionError("%s.version: an exact x.y.z pin, got %r" % (r.where, version))
    pin = PlaywrightPin(version, r.get("licence", str))
    r.done()
    return pin


def _placement(pid: str, r: _Reader) -> Placement:
    width, height = _positive(r, "width"), _positive(r, "height")
    if width % 2 or height % 2:
        raise ConventionError("%s: yuv420p needs even width and height, got %dx%d" % (r.where, width, height))
    d = r.sub("duration_s")
    dmin, dmax = _positive(d, "min", float), _positive(d, "max", float)
    d.done()
    if dmin > dmax:
        raise ConventionError("%s.duration_s: min %s > max %s" % (r.where, dmin, dmax))
    max_bytes, target_bytes = _positive(r, "max_bytes"), _positive(r, "target_bytes")
    if target_bytes > max_bytes:
        raise ConventionError("%s: target_bytes above max_bytes" % r.where)
    s = r.sub("safe_zone")
    zone = SafeZone(_share(s, "top"), _share(s, "bottom"), _share(s, "left"), _share(s, "right"))
    s.done()
    if zone.top + zone.bottom >= 1.0 or zone.left + zone.right >= 1.0:
        raise ConventionError("%s.safe_zone: the margins leave no frame" % r.where)
    p = Placement(pid, r.get("description", str), width, height, _positive(r, "fps"),
                  _choice(r, "container", CONTAINERS), _choice(r, "codec", CODECS),
                  _choice(r, "pix_fmt", PIX_FMTS), r.get("audio", bool), dmin, dmax,
                  max_bytes, target_bytes, _positive(r, "min_bitrate_kbps"), zone)
    r.done()
    return p


PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")


def _encode(r: _Reader) -> EncodeRules:
    enc = EncodeRules(_choice(r, "preset", PRESETS), _positive(r, "threads"), _positive(r, "bitrate_kbps"),
                      _positive(r, "bufsize_kbps"), _positive(r, "keyint_s", float))
    r.done()
    return enc


def _ids(r: _Reader, key: str) -> Tuple[str, ...]:
    vals = r.get(key, list)
    if not vals or not all(isinstance(v, str) and v for v in vals) or len(set(vals)) != len(vals):
        raise ConventionError("%s.%s: a non-empty list of unique names" % (r.where, key))
    return tuple(vals)


def _licences(r: _Reader) -> LicenceRules:
    lic = LicenceRules(_ids(r, "allowed"), _ids(r, "generator_models"))
    r.done()
    return lic


def _checks(r: _Reader) -> CheckRules:
    up = r.get("max_upscale", float)
    if up < 1.0:
        raise ConventionError("%s.max_upscale: must be >= 1.0, got %s" % (r.where, up))
    opacity = r.get("visible_opacity", float)
    if not 0.0 < opacity <= 1.0:
        raise ConventionError("%s.visible_opacity: must be in (0, 1]" % r.where)
    ratios = {k: r.get(k, float) for k in ("contrast_min", "cta_text_contrast_min", "cta_fill_contrast_min")}
    for k, v in ratios.items():
        if not 1.0 <= v <= 21.0:
            raise ConventionError("%s.%s: a WCAG ratio is 1-21, got %s" % (r.where, k, v))
    c = CheckRules(up, opacity, _nonneg(r, "safe_zone_tolerance_px"), _share(r, "min_text_share"),
                   _share(r, "cta_min_height_share"), _share(r, "cta_min_text_share"), ratios["contrast_min"],
                   _share(r, "contrast_percentile"), ratios["cta_text_contrast_min"], ratios["cta_fill_contrast_min"],
                   _nonneg(r, "end_card_min_s"), _nonneg(r, "first_frame_min_std"),
                   _nonneg(r, "logo_aspect_tolerance"), _share(r, "batch_distinct_min"),
                   _share(r, "hero_clash_max"))
    r.done()
    return c


def _repair(r: _Reader) -> RepairRules:
    fit = r.get("fit_margin", float)
    if not 0.5 <= fit <= 1.0:
        raise ConventionError("%s.fit_margin: must be in [0.5, 1], got %s" % (r.where, fit))
    default = _choice(r, "default", REPAIR_ACTIONS)
    actions = {}
    for cid, action in sorted(r.get("actions", dict).items()):
        if not re.match(r"^banner\.[a-z][a-z0-9_]*$", str(cid)):
            raise ConventionError("%s.actions.%s: expected a banner.* check id" % (r.where, cid))
        if action not in REPAIR_ACTIONS:
            raise ConventionError("%s.actions.%s: expected one of %s" % (r.where, cid, list(REPAIR_ACTIONS)))
        actions[cid] = action
    rep = RepairRules(_positive(r, "max_renders"), _positive(r, "max_measure_passes"), fit, default, actions)
    r.done()
    return rep


def _nonneg(r: _Reader, key: str) -> float:
    val = r.get(key, float)
    if val < 0:
        raise ConventionError("%s.%s: must be >= 0, got %s" % (r.where, key, val))
    return val


def parse(data: dict) -> BannerRules:
    root = _Reader(data, FILE)
    root.get("version", int)
    tools = root.sub("tools")
    ffmpeg = _tool("ffmpeg", tools.sub("ffmpeg"), FFMPEG_EXES)
    chromium = _tool("chromium", tools.sub("chromium"), CHROMIUM_EXES)
    playwright = _playwright(tools.sub("playwright"))
    tools.done()
    encode = _encode(root.sub("encode"))
    licences = _licences(root.sub("licences"))
    checks = _checks(root.sub("checks"))
    repair = _repair(root.sub("repair"))
    placements = {}
    for pid, raw in sorted(root.get("placements", dict).items()):
        if not _ID.match(str(pid)):
            raise ConventionError("%s.placements.%s: placement id must be snake_case" % (FILE, pid))
        placements[pid] = _placement(pid, _Reader(raw, "%s.placements.%s" % (FILE, pid)))
        if placements[pid].min_bitrate_kbps > encode.bitrate_kbps:
            raise ConventionError("%s.placements.%s.min_bitrate_kbps: above encode.bitrate_kbps (%d): every "
                                  "encode would fail" % (FILE, pid, encode.bitrate_kbps))
    if not placements:
        raise ConventionError("%s: define at least one placement" % FILE)
    root.done()
    return BannerRules(ffmpeg, chromium, playwright, encode, licences, checks, repair, placements)


def load(conventions_dir: Optional[Path] = None) -> BannerRules:
    return parse(_read_yaml((conventions_dir or DEFAULT_DIR) / FILE))
