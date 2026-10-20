"""Record real ComfyUI replies as fixtures for the pure gen.core tests.

    artpipe py tests/fixtures/comfy_record.py

Starts the pipeline's ComfyUI (gen.comfy), runs a few model-free graphs and
saves what the API answered into tests/fixtures/comfy/: /history entries
(fresh, fully cached, failed at run time) and POST /prompt refusals. Re-run
it after a ComfyUI version bump and diff the files: a changed shape shows up
as a diff, and the core tests then run against the new shape.

Machine paths in /system_stats are replaced by "<path>" before saving.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gen import comfy  # noqa: E402
from gen.core import manifest, rules  # noqa: E402

OUT = Path(__file__).resolve().parent / "comfy"


def graph(color=0xFF8800, prefix="record/img"):
    return {"1": {"class_type": "EmptyImage",
                  "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": color}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": prefix}}}


def scrub(value, swaps):
    """Replace this machine's folders in every string (exception messages and
    tracebacks quote absolute paths, some with doubled backslashes)."""
    if isinstance(value, dict):
        return {k: scrub(v, swaps) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, swaps) for v in value]
    if isinstance(value, str):
        for path, token in swaps:
            for form in (path, path.replace("\\", "\\\\"), path.replace("\\", "/")):
                value = value.replace(form, token)
    return value


def save(name, data):
    # Most specific first: the ComfyUI install and the repo both sit under home.
    swaps = [(str(comfy.comfy_dir()), "<comfyui>"), (str(ROOT), "<repo>"), (str(Path.home()), "<home>")]
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(scrub(data, swaps), indent=2, sort_keys=True) + "\n"
    (OUT / name).write_text(text, encoding="utf-8")
    print("wrote", (OUT / name).relative_to(ROOT).as_posix())


def main():
    r = rules.load().comfyui
    started = comfy.start(r)
    try:
        c = comfy.Client(r.port)
        cid = "record-" + uuid.uuid4().hex
        # A colour no earlier run used, so the first run really executes.
        fresh = graph(color=int(uuid.uuid4().hex[:6], 16))

        def run(g):
            pid, refused = comfy.queue(c, g, cid)
            assert refused is None, refused
            return {"graph": g, "entry": comfy.wait(c, pid, 60)}

        save("history_success.json", run(fresh))
        save("history_cached.json", run(fresh))

        # Fails at run time, not validation: LoadImage only checks the file exists.
        bad = comfy.WORK / "input" / "artpipe_corrupt.png"
        bad.write_bytes(b"this is not a png\n")
        g = {"1": {"class_type": "LoadImage", "inputs": {"image": bad.name}},
             "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "record/bad"}}}
        save("history_execution_error.json", run(g))

        refusals = {}
        g = graph()
        g["2"]["inputs"]["images"] = ["9", 0]
        refusals["missing_link"] = comfy.queue(c, g, cid)[1]
        g = graph()
        g["1"]["inputs"]["width"] = 0
        refusals["value_below_min"] = comfy.queue(c, g, cid)[1]
        refusals["unknown_node"] = comfy.queue(c, {"1": {"class_type": "NoSuchNode", "inputs": {}}}, cid)[1]
        save("prompt_refusals.json", {k: {"status": v[0], "body": v[1]} for k, v in refusals.items()})

        stats = c.get_json("/system_stats")
        stats["system"]["argv"] = ["main.py"] + manifest.server_argv(stats["system"]["argv"])
        save("system_stats.json", stats)
    finally:
        if started:
            comfy.stop()


if __name__ == "__main__":
    main()
