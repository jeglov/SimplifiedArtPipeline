"""What a kit check sees: the template, the kit, the font registry, the rules, the locales asked for."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from banners.core.kit import Font, Kit
from banners.core.rules import BannerRules
from banners.core.template import Template


@dataclass(frozen=True)
class KitContext:
    template: Template
    kit: Kit
    fonts: Dict[str, Font]
    rules: BannerRules
    locales: Tuple[str, ...]
    placements: Tuple[str, ...]


def plural(n: int, one: str, many: str = "") -> str:
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))
