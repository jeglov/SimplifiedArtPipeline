"""Make a saved Maya ASCII (.ma) file byte-stable: same scene, same bytes.

Two kinds of noise, both probed on Maya 2023:
  * Lines written differently on EVERY save: the save time, a random file
    UUID, and fresh UUIDs for nodes Maya recreates each session. Dropped:
    Maya regenerates UUIDs on load, so no asset data is lost.
  * "Next available" connections (connectAttr ... -na) into the same plug,
    e.g. meshes joining a shading group's dagSetMembers, come out in a
    varying ORDER on every save of a multi-mesh scene. Each contiguous run
    into one destination is sorted. The order only decides member indices
    inside the set, which nothing renders or exports differently.

Without this, a fixed multi-mesh asset (any LOD set) got a new sha256 on
every run, and so did its report.

Pure Python, bytes in / bytes out: testable without Maya.
"""
from __future__ import annotations

import re
from typing import List

_VOLATILE = re.compile(rb'^(//Last modified: .*|fileInfo "UUID" ".*";|\s*rename -uid ".*";)\r?\n',
                       re.MULTILINE)
_NEXT_AVAILABLE = re.compile(rb'^connectAttr "[^"]+" "([^"]+)" -na;\r?$')


def canonicalize_ma(data: bytes) -> bytes:
    data = _VOLATILE.sub(b"", data)
    lines = data.split(b"\n")
    out: List[bytes] = []
    run: List[bytes] = []
    run_dest = None

    def flush() -> None:
        out.extend(sorted(run))
        run.clear()

    for line in lines:
        m = _NEXT_AVAILABLE.match(line)
        dest = m.group(1) if m else None
        if dest is None or dest != run_dest:
            flush()
            run_dest = dest
        if dest is None:
            out.append(line)
        else:
            run.append(line)
    flush()
    return b"\n".join(out)
