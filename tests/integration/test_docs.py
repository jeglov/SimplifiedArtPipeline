"""docs/conventions.md must match the conventions and the registry.

The tables in the doc are generated (validate/maya/gen_docs.py). If this
fails, a rule, check or fix changed without the doc: run `artpipe docs`.
No Maya session needed (the generator never starts Maya), but it imports
the check modules, so it lives with the mayapy tests.
"""
from validate.maya import gen_docs


def test_conventions_doc_is_up_to_date():
    assert gen_docs.main(["--check"]) == 0, "docs/conventions.md is stale: run `artpipe docs`"


def test_every_generated_block_is_present_and_known():
    text = gen_docs.DOC.read_text(encoding="utf-8")
    from validate.core import conventions as conv
    gen_docs.render(text, conv.load())  # raises on a missing or unknown block


def test_unity_doc_has_every_generated_block():
    from validate.core import conventions as conv
    text = gen_docs.UNITY_DOC.read_text(encoding="utf-8")
    gen_docs.render(text, conv.load(), gen_docs.UNITY_SECTIONS, gen_docs.UNITY_DOC.name)
