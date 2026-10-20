"""banners.core.video and banners.core.timeline: encoder arguments, ffprobe facts, keyframes. Pure Python, <1 s."""
from __future__ import annotations

import pytest

from banners.core import ToolError
from banners.core import rules as br
from banners.core import timeline as tl
from banners.core import video

ENC = br.load().encode


def test_encode_args_pin_threads_and_constant_bitrate():
    argv = video.encode_args("ffmpeg", "frames/f%05d.png", 30, "out.mp4", ENC)
    joined = " ".join(argv)
    assert argv[argv.index("-threads") + 1] == "1"            # never 0 (= per core); VBV is only stable on 1
    for flag in ("-b:v", "-minrate", "-maxrate"):
        assert argv[argv.index(flag) + 1] == "5000k"
    assert "nal-hrd=cbr:keyint=60:min-keyint=60" in joined     # 2 s at 30 fps
    assert "-an" in argv and "+bitexact" in argv and argv[argv.index("-map_metadata") + 1] == "-1"
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p" and argv[-1] == "out.mp4"


def test_keyint_never_zero():
    enc = video.EncodeRules("medium", 1, 1000, 2000, 0.01)
    assert "keyint=1:" in " ".join(video.encode_args("ffmpeg", "f%05d.png", 30, "o.mp4", enc))


def test_decode_selects_each_frame_once_in_order():
    argv = video.decode_args("ffmpeg", "in.mp4", [239, 0, 0, 120], "d%05d.png")
    assert argv[argv.index("-vf") + 1] == "select=eq(n\\,0)+eq(n\\,120)+eq(n\\,239)"
    assert argv[argv.index("-pix_fmt") + 1] == "rgb24"


def probe(n=240, fps=30, interval=None, codec="h264"):
    step = interval or 1.0 / fps
    packets = [{"pts_time": "%.6f" % (i * step), "flags": "K__" if i % 60 == 0 else "___"} for i in range(n)]
    return {"streams": [{"codec_type": "video", "codec_name": codec, "profile": "High", "pix_fmt": "yuv420p",
                         "width": 1080, "height": 1920, "r_frame_rate": "%d/1" % fps, "avg_frame_rate": "%d/1" % fps}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "%.6f" % (n * step)},
            "packets": packets}


def test_facts_of_a_constant_rate_file():
    f = video.facts(probe(), {"streams": [{"codec_type": "video"}]}, 5_000_000)
    assert (f["codec"], f["width"], f["height"], f["frames"], f["duration_s"]) == ("h264", 1080, 1920, 240, 8.0)
    assert f["frame_intervals_s"] == [0.033333] or f["frame_intervals_s"] == [0.033333, 0.033334]
    assert f["bitrate_kbps"] == 5000.0 and f["audio_streams"] == 0 and f["keyframes"] == 4


def test_facts_see_a_dropped_frame():
    p = probe(n=10)
    p["packets"].pop(5)   # a gap: one interval is twice as long
    f = video.facts(p, {"streams": []}, 1000)
    assert max(f["frame_intervals_s"]) == pytest.approx(2 / 30, abs=1e-5)


def test_facts_count_audio_from_the_unfiltered_stream_list():
    f = video.facts(probe(), {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}, 1)
    assert f["audio_streams"] == 1


def test_no_video_stream_is_a_tool_error():
    with pytest.raises(ToolError, match="no video stream"):
        video.facts({"streams": [{"codec_type": "audio"}]}, {}, 1)


# --- timeline ---------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(set(tl.EASINGS) - {"hold"}))
def test_easings_start_at_0_and_end_at_1(name):
    assert tl.EASINGS[name](0.0) == pytest.approx(0.0) and tl.EASINGS[name](1.0) == pytest.approx(1.0)


def test_out_back_overshoots_then_settles():
    peak = max(tl.EASINGS["out_back"](i / 100) for i in range(101))
    assert 1.05 < peak < 1.15


def test_value_holds_outside_the_keys_and_interpolates_between():
    track = (tl.Key(1.0, 0.0), tl.Key(2.0, 10.0, "linear"))
    assert tl.value_at(track, 0.0) == 0.0 and tl.value_at(track, 1.5) == 5.0 and tl.value_at(track, 9.0) == 10.0
    assert tl.value_at(track, 2.0) == 10.0          # boundary: exactly on the last key


def test_hold_jumps_at_its_key():
    track = (tl.Key(0.0, 0.0), tl.Key(1.0, 1.0, "hold"))
    assert tl.value_at(track, 0.999) == 0.0 and tl.value_at(track, 1.0) == 1.0


def test_frame_times_and_states():
    assert len(tl.frame_times(8.0, 30)) == 240 and tl.frame_times(8.0, 30)[-1] == pytest.approx(239 / 30)
    frames = tl.states({"a": {"opacity": (tl.Key(0.0, 0.0), tl.Key(1.0, 2.0))}}, 2.0, 2)
    assert [f["a"]["opacity"] for f in frames] == [0.0, 1.0, 1.0, 1.0]   # clamped to [0, 1]
    assert frames[0]["a"]["scale"] == 1.0                                  # untracked: default
