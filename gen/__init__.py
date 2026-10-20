"""artpipe generation stage (Phase 3): ComfyUI driven headless, with provenance.

gen.core   pure logic (rules, workflows, history parsing, manifests); no I/O
           beyond reading files, testable on any Python 3.9+.
gen.comfy  the ComfyUI server process and its HTTP API (stdlib only).
gen.cli    `artpipe gen run|server`.
"""
