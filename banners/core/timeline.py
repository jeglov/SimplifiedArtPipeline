"""Keyframed motion, evaluated in Python for every frame.

A layer animates five properties: opacity, scale, x and y (an offset as a
share of the frame width / height) and rotate (degrees). Each is a list of
keys [time_s, value, easing]; the easing shapes the segment that ENDS at
that key. Before the first key the property holds the first value, after
the last key the last value.

The browser never interpolates anything: the page applies the numbers this
module computes. So motion is defined (and tested) once, here, and a frame
is a pure function of its index.

Pure Python 3.9.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

PROPERTIES = {"opacity": 1.0, "scale": 1.0, "x": 0.0, "y": 0.0, "rotate": 0.0}
# Back-ease overshoot (Penner's constant): ~10 % past the target, then settle.
_BACK = 1.70158


def _out_back(t: float) -> float:
    u = t - 1.0
    return 1.0 + (_BACK + 1.0) * u ** 3 + _BACK * u ** 2


EASINGS = {
    "linear": lambda t: t,
    "hold": lambda t: 0.0,                      # stays at the previous value, jumps at the key
    "ease_in": lambda t: t ** 3,
    "ease_out": lambda t: 1.0 - (1.0 - t) ** 3,
    "ease_in_out": lambda t: 4 * t ** 3 if t < 0.5 else 1.0 - (-2 * t + 2) ** 3 / 2,
    "out_back": _out_back,
    "sine": lambda t: 0.5 - math.cos(math.pi * t) / 2,
}


@dataclass(frozen=True)
class Key:
    t: float
    value: float
    easing: str = "linear"


Track = Tuple[Key, ...]


def value_at(track: Sequence[Key], t: float) -> float:
    if t <= track[0].t:
        return track[0].value
    for a, b in zip(track, track[1:]):
        if t < b.t:
            u = (t - a.t) / (b.t - a.t)
            return a.value + (b.value - a.value) * EASINGS[b.easing](u)
    return track[-1].value


def state_at(tracks: Mapping[str, Track], t: float) -> Dict[str, float]:
    """Every property at time t (defaults for the ones without a track)."""
    out = dict(PROPERTIES)
    for prop, track in tracks.items():
        out[prop] = value_at(track, t)
    out["opacity"] = min(1.0, max(0.0, out["opacity"]))
    return {k: round(v, 6) for k, v in out.items()}


def frame_times(duration_s: float, fps: int) -> List[float]:
    """Time of each frame: frame n shows t = n / fps (frame count = round(duration x fps))."""
    return [n / float(fps) for n in range(int(round(duration_s * fps)))]


def states(layers: Mapping[str, Mapping[str, Track]], duration_s: float, fps: int) -> List[Dict[str, Dict[str, float]]]:
    """[frame][layer id] -> state."""
    return [{lid: state_at(tracks, t) for lid, tracks in layers.items()} for t in frame_times(duration_s, fps)]


def settled_from(track: Sequence[Key]) -> float:
    """Time of the last key: from then on the property doesn't move."""
    return track[-1].t if track else 0.0
