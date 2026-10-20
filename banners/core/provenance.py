"""provenance.json next to every banner: what it was made from, by what, under which licences.

The licence chain of a creative in one file: every kit image's sidecar
(kind, licence, the models with their licences and the run they came
from), every font's licence, the pinned tools, the template, and the
output's sha256. `licences` is the de-duplicated list a legal review reads
first. Pure Python 3.9 (conventions/banner_provenance_banner.schema.json).
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

SCHEMA_VERSION = "1.0"
TOOL = "artpipe.banners.provenance"


def build(*, template_id: str, template_sha256: str, kit, fonts: Mapping[str, Any], rules, placement: str,
          locale: str, mp4_sha256: str, input_hash: str = None) -> Dict[str, Any]:
    images = {}
    licences = set()
    for slot, img in sorted(kit.images.items()):
        rec = img.record or {}
        models = [{"id": m.get("id"), "licence": m.get("licence"), "sha256": m.get("sha256")}
                  for m in rec.get("models") or []]
        images[slot] = {"file": img.path.name, "sha256": rec.get("output_sha256"), "kind": rec.get("kind"),
                        "licence": img.licence, "models": models,
                        "run_id": (rec.get("from") or {}).get("run_id")}
        licences.add(img.licence)
        licences.update("model:%s" % m["licence"] for m in models)
    used_fonts = {}
    for slot, fid in sorted(kit.fonts.items()):
        f = fonts[fid]
        used_fonts[slot] = {"id": fid, "family": f.family, "sha256": f.sha256, "licence": f.licence,
                            "source": f.source}
        licences.add(f.licence)
    tools = {"chromium": rules.chromium.version, "ffmpeg": rules.ffmpeg.version,
             "playwright": rules.playwright.version}
    licences.update(("tool:%s" % rules.ffmpeg.licence, "tool:%s" % rules.chromium.licence,
                     "tool:%s" % rules.playwright.licence))
    return {"schema_version": SCHEMA_VERSION, "tool": TOOL,
            "banner": {"kit": kit.id, "game": kit.name, "fictional": kit.fictional, "template": template_id,
                       "template_sha256": template_sha256, "placement": placement, "locale": locale,
                       "mp4_sha256": mp4_sha256, "input_hash": input_hash},
            "images": images, "fonts": used_fonts, "tools": tools, "licences": sorted(licences)}
