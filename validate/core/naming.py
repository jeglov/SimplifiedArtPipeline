"""Naming rules, driven entirely by conventions/naming.schema.json.

The schema is the single source of truth. This module only compiles its
`$defs.<kind>.pattern` regexes and gives agent-friendly error messages.
Change a convention by editing the JSON, never by editing Python.

Pure Python (no maya import).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

# Separate from the schema on purpose: the LOD suffix has semantics (an int
# level) that the validator needs, not just a yes/no match.
_LOD_SUFFIX = re.compile(r"_LOD([0-9]+)$")


@dataclass(frozen=True)
class NameRule:
    kind: str
    pattern: "re.Pattern[str]"
    description: str
    examples: tuple
    invalid_examples: tuple


@dataclass(frozen=True)
class NameCheck:
    """Result of checking one name. `message` is written for an LLM/agent:
    it says what was expected and gives a concrete valid example."""

    ok: bool
    kind: str
    name: str
    message: str


class NamingRules:
    def __init__(self, schema: Mapping[str, Any]) -> None:
        defs = schema.get("$defs")
        if not isinstance(defs, dict) or not defs:
            raise ValueError("naming schema has no $defs")
        rules: Dict[str, NameRule] = {}
        for kind, d in defs.items():
            if "pattern" not in d:
                raise ValueError("naming schema $defs.%s has no pattern" % kind)
            rules[kind] = NameRule(
                kind=kind,
                pattern=re.compile(d["pattern"]),
                description=d.get("description", ""),
                examples=tuple(d.get("examples", ())),
                invalid_examples=tuple(d.get("x-invalid-examples", ())),
            )
        self._rules = rules

    @property
    def kinds(self) -> List[str]:
        return sorted(self._rules)

    def rule(self, kind: str) -> NameRule:
        try:
            return self._rules[kind]
        except KeyError:
            raise KeyError("unknown name kind %r, expected one of %s" % (kind, self.kinds))

    def check(self, kind: str, name: str) -> NameCheck:
        rule = self.rule(kind)
        # fullmatch, not match: Python's '$' also matches before a trailing
        # '\n', so "SM_Crate\n" would pass re.match("^...$").
        if rule.pattern.fullmatch(name):
            return NameCheck(True, kind, name, "ok")
        example = rule.examples[0] if rule.examples else "n/a"
        msg = "%r is not a valid %s name. Expected pattern %s (%s) e.g. %r" % (
            name, kind, rule.pattern.pattern, rule.description, example)
        return NameCheck(False, kind, name, msg)

    @staticmethod
    def lod_level(name: str) -> Optional[int]:
        """'SM_Crate_A_LOD2' -> 2, 'SM_Crate_A' -> None."""
        m = _LOD_SUFFIX.search(name)
        return int(m.group(1)) if m else None

    @staticmethod
    def strip_lod(name: str) -> str:
        """'SM_Crate_A_LOD2' -> 'SM_Crate_A' (the asset base name)."""
        return _LOD_SUFFIX.sub("", name)
