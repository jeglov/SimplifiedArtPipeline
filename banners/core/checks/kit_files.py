"""kit.files: every font and image the kit uses exists and is the file its record pins.

Images are pinned by their provenance sidecar (<file>.provenance.json),
fonts by banners/fonts/fonts.yaml. The sha256 in the record is what the
licence record talks about: a logo replaced on disk without a new record
would ship under someone else's licence record. It also makes the banner's input hash honest (reuse is
keyed on these hashes).
"""
from __future__ import annotations

import hashlib

from banners.core.checks.context import plural
from validate.core.result import CheckResult, Severity

ID = "kit.files"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every kit file exists and matches its pinned sha256."


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(ctx) -> CheckResult:
    files = [("image.%s" % slot, img.path, img.sha256) for slot, img in sorted(ctx.kit.images.items())]
    files += [("font.%s" % fid, ctx.fonts[fid].path, ctx.fonts[fid].sha256)
              for fid in sorted(set(ctx.kit.fonts.values())) if fid in ctx.fonts]
    bad = []
    for label, path, pinned in files:
        if not path.is_file():
            bad.append("%s: missing %s" % (label, path.name))
        elif pinned is None:
            bad.append("%s: %s has no readable provenance record" % (label, path.name))
        elif sha256_file(path) != pinned:
            bad.append("%s: %s changed (sha256 differs from the record)" % (label, path.name))
    if bad:
        return CheckResult(ID, False, SEVERITY, "%s of %d: %s" % (plural(len(bad), "bad file"), len(files), bad[0]),
                           FIXABLE, {"files": len(files), "bad": len(bad)}, offenders=bad)
    return CheckResult(ID, True, SEVERITY, "%d files match their records" % len(files), FIXABLE,
                       {"files": len(files), "bad": 0})
