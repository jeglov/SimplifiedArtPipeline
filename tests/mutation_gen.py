"""Mutation pass over the generation logic (Phase 3 steps 3.4-3.8).

    artpipe py tests/mutation_gen.py

Plants one bug at a time, runs the fast generation tests, restores the file,
and lists the mutants that survived (a survivor means a missing test). Exit
code 1 if any survived. Not collected by pytest (no test_ prefix).

Bytecode caching is off: Python trusts a .pyc when the source size and its
whole-second mtime match, so a same-length mutant restored within the same
second would otherwise keep running (probed: it hid a survivor).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ["tests/test_glb_model.py", "tests/test_winding_texel_ingest.py", "tests/test_gen_repair.py",
         "tests/test_gen_runner.py", "tests/test_gen_core.py", "tests/test_generate.py", "tests/test_gen_concept.py"]
MUTANTS = [
    # model checks (3.4)
    ("gen/core/model.py", "0 < tris <= cls.tris_max", "0 < tris <= cls.tris_max + 1", "budget off by one"),
    ("gen/core/model.py", "sum(1 for n in uses.values() if n > 2)", "sum(1 for n in uses.values() if n > 3)", "non-manifold = 3+ uses"),
    ("gen/core/model.py", "add(\"model.closed\", open_edges == 0,", "add(\"model.closed\", open_edges <= 3,", "smallest hole tolerated"),
    ("gen/core/model.py", "ok = shared <= uv_overlap_max_share", "ok = shared < uv_overlap_max_share * 10", "overlap threshold x10"),
    ("gen/core/model.py", "has_base and has_normal and len(glb.images) >= 2", "has_base and len(glb.images) >= 1", "normal map optional"),
    # ingest (3.5)
    ("gen/core/ingest.py", "key = (round(u, 7), round(1.0 - v, 7))", "key = (round(u, 7), round(v, 7))", "no V flip"),
    ("gen/core/ingest.py", "if len(set(corners)) < 3:", "if len(set(corners)) < 2:", "degenerate triangles kept"),
    # winding / texel (3.5)
    ("validate/core/winding.py", "outward = [(f, b) for f, v, b in found if v > 0]", "outward = [(f, b) for f, v, b in found if v < 0]", "outward sign flipped"),
    ("validate/core/winding.py", "enclosed = any(all(olo[i] <= lo[i] and hi[i] <= ohi[i] for i in range(3))", "enclosed = any(all(olo[i] <= lo[i] for i in range(3))", "enclosure checks one corner"),
    ("validate/core/texel.py", "(d > mesh * OFF_FACTOR or d * OFF_FACTOR < mesh)", "(d > mesh * OFF_FACTOR)", "only too-dense faces flagged"),
    # repair (3.6)
    ("gen/core/repair.py", "if attempt >= policy.max_attempts:", "if attempt > policy.max_attempts:", "one attempt too many"),
    ("gen/core/repair.py", "first = failed[0]", "first = failed[-1]", "last failure decides"),
    ("gen/core/repair.py", "seed = base_seed + (n - 1) * policy.seed_step", "seed = base_seed + n * policy.seed_step", "seed sequence shifted"),
    ("gen/core/repair.py", "if not r.get(\"passed\") and r.get(\"severity\") == \"error\"]", "if not r.get(\"passed\")]", "warnings block"),
    # generate (3.7) and scrub (3.8)
    ("pipeline/generate.py", "if code != 0:\n            return stopped_report(asset_class, \"model\"", "if code == 2:\n            return stopped_report(asset_class, \"model\"", "handed-over model goes on"),
    ("gen/core/manifest.py", "flags=re.IGNORECASE)", "flags=0)", "case-sensitive scrub"),
    ("gen/core/manifest.py", "re.escape(form) + r\"(?![\\w-])\"", "re.escape(form)", "no name boundary"),
    ("gen/cli.py", "error = mf.scrub(error, machine_swaps(env))", "error = error", "manifest not scrubbed"),
]


def main():
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    survived = []
    for path, old, new, what in MUTANTS:
        f = ROOT / path
        with open(f, encoding="utf-8", newline="") as fh:
            src = fh.read()
        if src.count(old) != 1:
            print("STALE    %-28s %s (pattern found %d times: update the mutant)" % (path, what, src.count(old)))
            survived.append(what)
            continue
        try:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"] + TESTS,
                               cwd=str(ROOT), capture_output=True, text=True, env=env)
        finally:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
        killed = r.returncode != 0
        print("%s %-28s %s" % ("KILLED  " if killed else "SURVIVED", path, what))
        if not killed:
            survived.append(what)
    print("survived:", survived)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
