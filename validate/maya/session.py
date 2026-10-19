"""Start headless Maya and open asset files.

Every maya import in this module is inside a function on purpose: `maya.cmds`
only gets its commands AFTER maya.standalone.initialize() has run. Importing
it at the top of a module that loads before start() gives you a cmds module
with no commands in it.
"""
from __future__ import annotations

import re
from pathlib import Path

# File extension -> Maya file type name used by cmds.file(type=...).
FILE_TYPES = {".ma": "mayaAscii", ".mb": "mayaBinary", ".fbx": "FBX"}


def start() -> None:
    """Boot Maya with no UI. Slow (~20 s), so call it once per process."""
    import maya.standalone
    maya.standalone.initialize(name="python")


def stop() -> None:
    """Shut Maya down cleanly. Skipping this makes mayapy 2023 print errors on exit."""
    import maya.standalone
    maya.standalone.uninitialize()


def open_scene(path: Path) -> None:
    """Replace the current scene with `path`. Raises on failure."""
    from maya import cmds

    ext = path.suffix.lower()
    if ext not in FILE_TYPES:
        raise ValueError("unsupported file type %r, expected one of %s" % (ext, sorted(FILE_TYPES)))

    flags = {"open": True, "force": True, "type": FILE_TYPES[ext], "ignoreVersion": True}
    if ext == ".fbx":
        cmds.loadPlugin("fbxmaya", quiet=True)
    else:
        # .ma/.mb files can contain script nodes: code that runs when the file
        # opens. The agent will open files it didn't author (AI generated,
        # outsourced), so never execute them.
        flags["executeScriptNodes"] = False

    cmds.file(new=True, force=True)
    cmds.file(path.resolve().as_posix(), **flags)


def save_scene(path: Path) -> None:
    """Save the current scene to `path` (.ma or .mb). Raises on failure."""
    from maya import cmds

    ext = path.suffix.lower()
    if ext == ".fbx":
        # FBX is written by validate.maya.export (explicit, verified,
        # canonical settings), never by a plain save.
        raise ValueError("save .ma/.mb only; FBX goes through validate.maya.export")
    if ext not in FILE_TYPES:
        raise ValueError("unsupported file type %r" % ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=path.resolve().as_posix())
    cmds.file(save=True, force=True, type=FILE_TYPES[ext])
    if ext == ".ma":
        _strip_volatile_lines(path)


# Lines Maya writes differently on EVERY save of the same scene: the save
# time, a random file UUID, and fresh UUIDs for nodes Maya recreates each
# session. Keeping them would give the fixed file (and so its report) a new
# sha256 on every run. Maya regenerates UUIDs on load; no asset data is lost.
_VOLATILE = re.compile(rb'^(//Last modified: .*|fileInfo "UUID" ".*";|\s*rename -uid ".*";)\r?\n',
                       re.MULTILINE)


def _strip_volatile_lines(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(_VOLATILE.sub(b"", data))
