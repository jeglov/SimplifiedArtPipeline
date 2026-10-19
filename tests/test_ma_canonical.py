"""validate.core.ma_canonical: same Maya scene, same .ma bytes (no Maya needed)."""
from __future__ import annotations

from validate.core.ma_canonical import canonicalize_ma

HEADER = (b'//Maya ASCII 2023 scene\n//Name: SM_Lods.ma\n//Last modified: Mon, Oct 19, 2026 05:01:02 PM\n'
          b'fileInfo "UUID" "1A2B";\ncreateNode transform -n "SM_Lods_LOD0";\n\trename -uid "AAAA";\n')


def ma(*connections: bytes) -> bytes:
    return HEADER + b"".join(connections) + b'connectAttr "a.msg" "b.msg";\n'


A = b'connectAttr "SM_Lods_LOD0Shape.iog" "M_LodsSG.dsm" -na;\n'
B = b'connectAttr "SM_Lods_LOD1Shape.iog" "M_LodsSG.dsm" -na;\n'
C = b'connectAttr "other.iog" "M_OtherSG.dsm" -na;\n'


def test_volatile_lines_are_dropped():
    out = canonicalize_ma(ma(A))
    assert b"Last modified" not in out and b"UUID" not in out and b"rename -uid" not in out
    assert b'//Name: SM_Lods.ma' in out and b'createNode transform' in out


def test_set_member_order_no_longer_matters():
    # Probed: Maya writes these two lines in either order on different saves.
    assert canonicalize_ma(ma(A, B)) == canonicalize_ma(ma(B, A))


def test_runs_into_different_plugs_are_not_merged():
    # Boundary: only CONTIGUOUS lines into the SAME destination are sorted.
    out = canonicalize_ma(ma(B, C, A))
    assert out.index(B) < out.index(C) < out.index(A)


def test_plain_connections_keep_their_place():
    out = canonicalize_ma(ma(B, A))
    assert out.endswith(b'connectAttr "a.msg" "b.msg";\n')


def test_crlf_files_are_handled():
    crlf = ma(B, A).replace(b"\n", b"\r\n")
    assert canonicalize_ma(crlf) == canonicalize_ma(ma(A, B).replace(b"\n", b"\r\n"))
    assert b"Last modified" not in canonicalize_ma(crlf)


def test_idempotent():
    once = canonicalize_ma(ma(B, A))
    assert canonicalize_ma(once) == once
