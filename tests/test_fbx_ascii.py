"""validate.core.fbx_ascii: deterministic ASCII FBX, without Maya.

The sample below has the structure Maya 2023 / FBX 2020.3.1 writes (probed),
cut down to what the canonicalizer touches, including a skinned mesh's bind
pose, which references model IDs outside Objects/Connections declarations.
Paths are fake: the real probe files held the author's own C:\\Users path.
"""
from __future__ import annotations

import pytest

from validate.core.fbx_ascii import FIRST_ID, FbxCanonicalError, canonicalize

TEMPLATE = """\
; FBX 7.7.0 project file
; ----------------------------------------------------

FBXHeaderExtension:  {
\tFBXHeaderVersion: 1004
\tCreationTimeStamp:  {
\t\tVersion: 1000
\t\tYear: {year}
\t\tMonth: 10
\t\tDay: 19
\t\tHour: 16
\t\tMinute: 19
\t\tSecond: {second}
\t\tMillisecond: {ms}
\t}
\tCreator: "FBX SDK/FBX Plugins version 2020.3.1"
\tSceneInfo: "SceneInfo::GlobalInfo", "UserData" {
\t\tProperties70:  {
\t\t\tP: "DocumentUrl", "KString", "Url", "", "{out}"
\t\t\tP: "SrcDocumentUrl", "KString", "Url", "", "{out}"
\t\t\tP: "Original|DateTime_GMT", "DateTime", "", "", "19/10/2026 14:19:{second}.{ms}"
\t\t\tP: "Original|FileName", "KString", "", "", "{out}"
\t\t\tP: "LastSaved|DateTime_GMT", "DateTime", "", "", "19/10/2026 14:19:{second}.{ms}"
\t\t\tP: "Original|ApplicationActiveProject", "KString", "", "", "C:\\Users\\{user}\\Documents\\maya\\projects"
\t\t\tP: "Original|ApplicationNativeFile", "KString", "", "", "C:\\Users\\{user}\\work\\SK_Hero.ma"
\t\t}
\t}
}
GlobalSettings:  {
\tProperties70:  {
\t\tP: "UpAxis", "int", "Integer", "",{up}
\t\tP: "UpAxisSign", "int", "Integer", "",{sign}
\t\tP: "UnitScaleFactor", "double", "Number", "",1
\t\tP: "TimeSpanStart", "KTime", "Time", "",1924423250
\t}
}
Documents:  {
\tCount: 1
\tDocument: {doc}, "", "Scene" {
\t\tRootNode: 0
\t}
}
Objects:  {
\tGeometry: {geo}, "Geometry::", "Mesh" {
\t\tVertices: *3 {
\t\t\ta: 0.{float_digits},1,2
\t\t}
\t}
\tModel: {model}, "Model::SK_Hero", "Mesh" {
\t}
\tModel: {joint}, "Model::root_jnt", "LimbNode" {
\t}
\tPose: {pose}, "Pose::bindPose1", "BindPose" {
\t\tPoseNode:  {
\t\t\tNode: {joint}
\t\t}
\t}
}
Connections:  {
\tC: "OO",{model},0
\tC: "OO",{geo},{model}
\tC: "OO",{joint},0
}
"""

RUN_A = dict(year=2026, second=19, ms=991, out="C:\\Users\\ann\\export\\SK_Hero.fbx", user="ann",
             doc=2193964656176, geo=2194061426672, model=2194020170320,
             joint=2194020154080, pose=2194054896208, up=1, sign=1,
             float_digits=2194061426672)  # = geo ID of run A: vertex data, not an ID
RUN_B = dict(RUN_A, second=21, ms=130, out="D:\\farm\\job7\\SK_Hero.fbx", user="build",
             doc=2193964655408, geo=2194061433024, model=2194020170999,
             joint=2194020111111, pose=2194054893504)


def raw(**kw) -> str:
    text = TEMPLATE
    for k, v in kw.items():
        text = text.replace("{%s}" % k, str(v))
    return text


def test_two_exports_of_one_scene_become_identical():
    # Different time, machine, user and memory addresses: same scene.
    a, info = canonicalize(raw(**RUN_A))
    b, _ = canonicalize(raw(**RUN_B))
    assert a == b
    assert info.objects == 5  # 1 document + 4 scene objects


def test_canonicalize_is_idempotent():
    once, _ = canonicalize(raw(**RUN_A))
    assert canonicalize(once)[0] == once


def test_crlf_input_gives_the_same_lf_output():
    lf, _ = canonicalize(raw(**RUN_A))
    crlf, _ = canonicalize(raw(**RUN_A).replace("\n", "\r\n"))
    assert crlf == lf and "\r" not in crlf


def test_paths_become_file_names_and_project_is_dropped():
    out, _ = canonicalize(raw(**RUN_A))
    assert '"DocumentUrl", "KString", "Url", "", "SK_Hero.fbx"' in out
    assert '"Original|ApplicationNativeFile", "KString", "", "", "SK_Hero.ma"' in out
    assert '"Original|ApplicationActiveProject", "KString", "", "", ""' in out
    assert "Users" not in out


def test_timestamps_are_pinned():
    out, _ = canonicalize(raw(**RUN_A))
    assert "Year: 2000" in out and "Millisecond: 0" in out and "2026" not in out
    assert out.count('"01/01/2000 00:00:00.000"') == 2


def test_ids_renumbered_in_declaration_order_everywhere():
    out, _ = canonicalize(raw(**RUN_A))
    doc, geo, model, joint, pose = (str(FIRST_ID + i) for i in range(5))
    assert "Document: %s," % doc in out
    assert 'Model: %s, "Model::SK_Hero"' % model in out
    assert 'C: "OO",%s,%s' % (geo, model) in out
    # Bind pose references a joint outside any declaration line: still renumbered.
    assert "Node: %s" % joint in out
    assert "Pose: %s," % pose in out


def test_non_id_numbers_are_untouched():
    out, _ = canonicalize(raw(**RUN_A))
    # KTime is a big integer but not an object ID.
    assert '"",1924423250' in out
    # Boundary: an ID's digits inside a float must not be rewritten.
    assert RUN_A["float_digits"] == RUN_A["geo"]
    assert "a: 0.%d,1,2" % RUN_A["float_digits"] in out


@pytest.mark.parametrize("up, sign, expected", [(1, 1, "y"), (2, 1, "z"), (1, -1, "-y")])
def test_up_axis_is_read_from_the_file(up, sign, expected):
    _, info = canonicalize(raw(**dict(RUN_A, up=up, sign=sign)))
    assert info.up_axis == expected
    assert info.unit_scale_cm == 1.0


# --- fail closed ---------------------------------------------------------------

def test_binary_fbx_is_refused():
    with pytest.raises(FbxCanonicalError, match="not an ASCII FBX"):
        canonicalize("Kaydara FBX Binary  \x00...")


def test_unknown_absolute_path_is_refused():
    # A path in a property we don't know about (e.g. a texture reference)
    # must not slip through just because we didn't list it.
    text = raw(**RUN_A).replace('\t\t\tP: "Original|FileName"',
                                '\t\t\tP: "RelativeFilename", "KString", "", "", "C:\\textures\\T_Hero_D.png"\n'
                                '\t\t\tP: "Original|FileName"')
    with pytest.raises(FbxCanonicalError, match="absolute paths"):
        canonicalize(text)


def test_connection_to_undeclared_id_is_refused():
    text = raw(**RUN_A).replace('\tC: "OO",{0},0'.format(RUN_A["joint"]), '\tC: "OO",4242424242424,0')
    with pytest.raises(FbxCanonicalError, match="undeclared"):
        canonicalize(text)


def test_duplicate_ids_are_refused():
    with pytest.raises(FbxCanonicalError, match="duplicate"):
        canonicalize(raw(**dict(RUN_A, joint=RUN_A["model"])))


def test_missing_timestamp_block_is_refused():
    text = raw(**RUN_A)
    start = text.index("\tCreationTimeStamp:")
    end = text.index("\t}\n", start) + 3
    with pytest.raises(FbxCanonicalError, match="CreationTimeStamp"):
        canonicalize(text[:start] + text[end:])


def test_missing_global_settings_is_refused():
    with pytest.raises(FbxCanonicalError, match="GlobalSettings"):
        canonicalize(raw(**RUN_A).replace('"UnitScaleFactor"', '"Other"'))
