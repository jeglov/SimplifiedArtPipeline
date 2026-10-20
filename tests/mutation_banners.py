"""Mutation pass over the banner logic (Phase 4).

    artpipe py tests/mutation_banners.py

Plants one bug at a time, runs the fast banner tests (no renderer), restores
the file, and lists the mutants that survived (a survivor means a missing
test). Exit code 1 if any survived. Not collected by pytest (no test_ prefix).
Bytecode caching is off (see tests/mutation_gen.py: a stale .pyc once hid a
survivor).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ["tests/test_banner_checks.py", "tests/test_banner_repair.py", "tests/test_banner_kit.py",
         "tests/test_banner_cutout.py", "tests/test_banner_background.py", "tests/test_banner_page.py",
         "tests/test_banner_template.py", "tests/test_banner_rules.py", "tests/test_banner_video.py",
         "tests/test_banner_batch.py", "tests/test_banner_tools.py", "tests/test_banner_art.py",
         "-m", "not render"]
C = "banners/core/checks/"
MUTANTS = [
    # banner checks (4.5)
    (C + "banner_safe_zone.py", 'if st["o"] < rules.visible_opacity:', 'if st["o"] <= 0.0:', "safe zone judges fading frames"),
    (C + "banner_safe_zone.py", 'if not inside(st["content"], zone, rules.safe_zone_tolerance_px):',
     'if not inside(st["content"], zone, 5.0):', "safe zone slack 5 px"),
    (C + "banner_text_fit.py", 'if m["lines"] > layer.max_lines:', 'if m["lines"] > layer.max_lines + 1:', "one extra line allowed"),
    (C + "banner_text_fit.py", "TOLERANCE_PX = 0.5", "TOLERANCE_PX = 1.5", "text fit slack 1.5 px"),
    (C + "banner_contrast.py", "if low < rules.contrast_min:", "if low < rules.contrast_min - 0.05:", "contrast bar 0.05 lower"),
    (C + "banner_contrast.py", "return round(percentile(ratios, rules.contrast_percentile), 2)",
     "return round(percentile(ratios, 0.5), 2)", "median instead of the worst 10 %"),
    (C + "banner_cta_contrast.py", "if text_ratio < rules.cta_text_contrast_min:", "if text_ratio < 1.5:", "CTA label bar 1.5:1"),
    (C + "banner_bitrate.py", "if rate < floor:", "if rate <= floor:", "bitrate floor inclusive"),
    (C + "banner_frame_rate.py", "TOLERANCE_S = 2e-6", "TOLERANCE_S = 0.04", "frame interval slack 40 ms"),
    (C + "banner_end_card.py", "if hold_s < rules.end_card_min_s:", "if hold_s < rules.end_card_min_s - 0.1:", "end card 0.1 s short"),
    (C + "banner_font_fallback.py", 'fallback = [f for f in used if not f.get("custom")]', "fallback = []", "fallback ignored"),
    (C + "banner_first_frame.py", "    if not shown:", "    if False:", "background-only first frame"),
    (C + "banner_logo.py", "if distortion > rules.logo_aspect_tolerance:", "if distortion > 0.1:", "logo stretch 10 % allowed"),
    (C + "banner_duration.py", "if frames != expected:", "if frames < expected - 1:", "one frame lost allowed"),
    (C + "banner_file_size.py", "if size > limit:", "if size > limit * 1.048576:", "MB read as MiB"),
    # kit checks (4.3, 4.8)
    (C + "kit_files.py", "elif sha256_file(path) != pinned:", "elif False:", "changed file accepted"),
    (C + "kit_licences.py", 'if m.get("licence") not in rules.generator_models)', "if False)", "any model licence shippable"),
    (C + "kit_background.py", 'if m.get("same_hue_share", 0.0) > limit:', 'if m.get("same_hue_share", 0.0) >= limit:', "clash limit exclusive"),
    # repair (4.6)
    ("banners/core/repair.py", 'stops = [cid for cid, a in actions.items() if a == "stop"]', "stops = []", "unfixable failures repaired"),
    ("banners/core/repair.py", 'actions[cid] = "with_text_fit" if named and named <= overflowing else "stop"',
     'actions[cid] = "with_text_fit"', "any safe-zone failure rides on text fit"),
    ("banners/core/repair.py", "return round(s * margin, 4) if s < 1.0 else 1.0", "return round(s, 4) if s < 1.0 else 1.0", "fit margin ignored"),
    # cut-out (4.3), page (4.4), batch (4.7), tools (4.1), encode (4.2)
    ("banners/core/cutout.py", "box = bbox(mask, lo + 1)", "box = bbox(mask, lo)", "zero-alpha ring cropped in"),
    ("banners/core/cutout.py", "(px[c] - (1.0 - a) * bg[c]) / a", "px[c] * 1.0", "no edge decontamination"),
    ("banners/core/page.py", "html.escape(strings[layer.text])", "strings[layer.text]", "strings not escaped"),
    ("banners/core/batch.py", '"checks": asdict(rules.checks), ', "", "hash ignores check rules"),
    ("banners/tools.py", "        if digest != pinned:", "        if False:", "swapped exe accepted"),
    ("banners/core/video.py", '"-threads", str(enc.threads)', '"-threads", "0"', "thread count per core"),
]


def main():
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    survived = []
    for path, old, new, what in MUTANTS:
        f = ROOT / path
        with open(f, encoding="utf-8", newline="") as fh:
            src = fh.read()
        if src.count(old) != 1:
            print("STALE    %-40s %s (pattern found %d times: update the mutant)" % (path, what, src.count(old)))
            survived.append(what)
            continue
        try:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"] + TESTS,
                               cwd=str(ROOT), capture_output=True, text=True, env=env)
        finally:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
        killed = r.returncode != 0
        print("%s %-40s %s" % ("KILLED  " if killed else "SURVIVED", path, what))
        if not killed:
            survived.append(what)
    print("survived:", survived)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
