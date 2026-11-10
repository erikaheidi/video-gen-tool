from pathlib import Path

from explainer.render.ffmpeg import Encoding, JoinSpec, join_filter, motion_filter, scene_clip_cmd
from explainer.render.scene import Layer, SceneLayers


def test_xfade_offsets():
    clips = [JoinSpec(Path("a"), 3.0), JoinSpec(Path("b"), 4.0, "fade", 0.5), JoinSpec(Path("c"), 2.0, "slideleft", 0.4)]
    filt, total = join_filter(clips, 30)
    assert "xfade=transition=fade:duration=0.500:offset=2.500" in filt
    assert "xfade=transition=slideleft:duration=0.400:offset=6.100" in filt
    assert total == 3 + 4 + 2 - 0.5 - 0.4


def test_hard_cut_uses_concat():
    filt, total = join_filter([JoinSpec(Path("a"), 2), JoinSpec(Path("b"), 2, "none", 0)], 30)
    assert "concat=n=2" in filt and total == 4


def test_motion_filters():
    assert motion_filter("none", 1080, 1920, 4) == "scale=1080:1920"
    assert "eval=frame" in motion_filter("zoom-in", 1080, 1920, 4)
    assert "1-t/4" in motion_filter("zoom-out", 1080, 1920, 4)
    assert "crop=1080:1920:x=" in motion_filter("pan-left", 1080, 1920, 4)


def test_scene_clip_cmd_shape():
    layers = SceneLayers("s", 3.0, Path("bg.png"), "zoom-in", Path("chrome.png"),
                         [Layer(Path("fg-0.png"), 0.2, 0.35), Layer(Path("fg-1.png"), 0.0, 0.0)])
    cmd = scene_clip_cmd(layers, Path("out.mp4"), width=1080, height=1080, fps=30, enc=Encoding())
    assert cmd.count("-i") == 4 and "-an" in cmd and cmd[-1] == "out.mp4"
    filt = cmd[cmd.index("-filter_complex") + 1]
    assert "fade=t=in:st=0.200:d=0.350:alpha=1" in filt
    assert filt.count("overlay") == 3
