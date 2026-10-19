"""Make an ASCII FBX deterministic: same scene in, same bytes out.

Probed on Maya 2023 / FBX plugin 2020.3.1: exporting one scene twice with the
same settings gives different bytes, in binary AND in ASCII, because the
file holds:
  * timestamps: CreationTimeStamp, Original|DateTime_GMT, LastSaved|DateTime_GMT
  * absolute paths: DocumentUrl, SrcDocumentUrl, Original|FileName (the
    output path), Original|ApplicationNativeFile (the source .ma) and
    Original|ApplicationActiveProject (the artist's Maya project). These
    are also a privacy leak in every shipped asset.
  * object IDs that are memory addresses ("Model: 2194020170320, ...").
    The object ORDER is stable, so IDs can be renumbered by order.

canonicalize() pins the timestamps, reduces paths to file names, and renumbers
every declared object ID in order of declaration. Then it re-checks the
result and fails closed: a leftover absolute path or a connection to an
undeclared ID raises instead of returning a file that only looks clean.

It also reads GlobalSettings (up axis, unit scale) from the file itself, so
callers verify what was WRITTEN, not what the exporter was asked to do.

Pure Python, no Maya. Same idea as stripping volatile lines from fixed .ma
files in Phase 1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

FIXED_DATETIME = "01/01/2000 00:00:00.000"
FIXED_TIMESTAMP = (("Year", 2000), ("Month", 1), ("Day", 1), ("Hour", 0),
                   ("Minute", 0), ("Second", 0), ("Millisecond", 0))
FIRST_ID = 1000000  # renumbered IDs: 1000000, 1000001, ... in declaration order

_HEADER = re.compile(r"^; FBX \d+\.\d+\.\d+ project file")
_TIMESTAMP_BLOCK = re.compile(r"(CreationTimeStamp:\s*\{)(.*?)(\})", re.S)
_DATETIME = re.compile(r'(P: "[^"]*DateTime_GMT", "DateTime", "", "", )"[^"]*"')
_PATH_PROP = re.compile(r'(P: "(DocumentUrl|SrcDocumentUrl|Original\|FileName|'
                        r'Original\|ApplicationNativeFile|Original\|ApplicationActiveProject)", '
                        r'"KString", "[^"]*", "", )"([^"]*)"')
# Other FBX writers put these in the header; Maya's ASCII doesn't. Pin them anyway.
_FILE_ID = re.compile(r'^(\s*FileId: ).*$', re.M)
_CREATION_TIME = re.compile(r'^(\s*CreationTime: ).*$', re.M)
# An object declaration inside Documents: / Objects:, e.g. '\tModel: 2194020170320, "Model::SM_Crate", "Mesh" {'
_DECLARATION = re.compile(r'^\t([A-Za-z]+): (\d+), "', re.M)
_SECTION = re.compile(r"^(Documents|Objects):\s*\{(.*?)^\}", re.M | re.S)
_CONNECTION = re.compile(r'^\s*C: "[A-Z]{2}",(\d+),(\d+)', re.M)
_BIG_INT = re.compile(r"(?<![\w.\-])(\d{6,})(?![\w.])")
_ABSOLUTE_PATH = re.compile(r'"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|Volumes|tmp)/)[^"]*"')
_GLOBAL = re.compile(r'P: "(UpAxis|UpAxisSign|UnitScaleFactor)", "[^"]*", "[^"]*", "",([-0-9.eE]+)')


class FbxCanonicalError(ValueError):
    """The file isn't an ASCII FBX we can make deterministic. Never ship it."""


@dataclass(frozen=True)
class FbxInfo:
    """Facts read from the canonical file itself."""
    up_axis: str          # "x" | "y" | "z"
    unit_scale_cm: float  # FBX UnitScaleFactor: 1.0 = centimetres
    objects: int          # declared objects (documents + scene objects)


def _file_name(path: str) -> str:
    return re.split(r"[\\/]", path)[-1] if path else ""


def canonicalize(text: str) -> Tuple[str, FbxInfo]:
    """Return (canonical text, facts). Idempotent: canonicalize(out) == out."""
    text = text.replace("\r\n", "\n")
    if not _HEADER.match(text):
        raise FbxCanonicalError("not an ASCII FBX (binary FBX can't be made deterministic)")

    # 1. timestamps
    def pin_block(m: "re.Match[str]") -> str:
        body = m.group(2)
        for key, value in FIXED_TIMESTAMP:
            body, n = re.subn(r"(\b%s: )\d+" % key, r"\g<1>%d" % value, body)
            if n != 1:
                raise FbxCanonicalError("CreationTimeStamp: expected one %s field, found %d" % (key, n))
        return m.group(1) + body + m.group(3)

    text, n = _TIMESTAMP_BLOCK.subn(pin_block, text)
    if n != 1:
        raise FbxCanonicalError("expected one CreationTimeStamp block, found %d" % n)
    text = _DATETIME.sub(r'\g<1>"%s"' % FIXED_DATETIME, text)
    text = _FILE_ID.sub(r"\g<1>0", text)
    text = _CREATION_TIME.sub(r'\g<1>"%s"' % FIXED_DATETIME, text)

    # 2. absolute paths -> file names (the active project carries no useful name)
    def strip_path(m: "re.Match[str]") -> str:
        keep = "" if m.group(2) == "Original|ApplicationActiveProject" else _file_name(m.group(3))
        return '%s"%s"' % (m.group(1), keep)

    text = _PATH_PROP.sub(strip_path, text)

    # 3. renumber object IDs in declaration order
    declared: List[str] = []
    for section in _SECTION.finditer(text):
        declared.extend(m.group(2) for m in _DECLARATION.finditer(section.group(2)))
    if not declared:
        raise FbxCanonicalError("no object declarations found in Documents/Objects")
    if len(set(declared)) != len(declared):
        raise FbxCanonicalError("duplicate object IDs in the file")
    ids: Dict[str, str] = {old: str(FIRST_ID + i) for i, old in enumerate(declared)}
    text = _BIG_INT.sub(lambda m: ids.get(m.group(1), m.group(1)), text)

    # 4. fail closed: re-check what we produce
    known = set(ids.values()) | {"0"}
    dangling = sorted({x for m in _CONNECTION.finditer(text) for x in m.groups()} - known)
    if dangling:
        raise FbxCanonicalError("connections to undeclared object IDs: %s" % dangling[:5])
    leaks = sorted(set(_ABSOLUTE_PATH.findall(text)))
    if leaks:
        raise FbxCanonicalError("absolute paths left in the file: %s" % leaks[:3])

    values = {k: v for k, v in _GLOBAL.findall(text)}
    try:
        axis = "xyz"[int(values["UpAxis"])]
        scale = float(values["UnitScaleFactor"])
        sign = int(values["UpAxisSign"])
    except (KeyError, ValueError, IndexError):
        raise FbxCanonicalError("GlobalSettings: missing or unreadable UpAxis/UpAxisSign/UnitScaleFactor")
    if sign != 1:
        axis = "-" + axis
    return text, FbxInfo(up_axis=axis, unit_scale_cm=scale, objects=len(declared))
