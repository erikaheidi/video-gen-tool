import json

import pytest

from explainer.loader import Workspace, render_status
from explainer.render.ffmpeg import probe
from explainer.render.pipeline import render_stills, render_video, validate

from .conftest import needs_ffmpeg


def test_validate_ok(ws: Workspace):
    d = validate(ws, ws.project("acme"), "demo")
    assert d.ok, d.errors


def test_validate_flags_overflow(ws: Workspace):
    proj = ws.project("acme")
    p = proj.video_path("demo")
    p.write_text(p.read_text().replace("title: Points", "title: " + " ".join(["verylongword"] * 80)))
    d = validate(ws, proj, "demo")
    assert any("overflow" in w or "shrunk" in w for w in d.warnings)


def test_stills(ws: Workspace, tmp_path):
    paths, _ = render_stills(ws, ws.project("acme"), "demo", out_dir=tmp_path / "st")
    names = [p.name for p in paths]
    assert "01-hook.png" in names and "sheet.png" in names
    assert len(names) == 5  # 3 scenes + template outro + sheet


@needs_ffmpeg
def test_render_end_to_end(ws: Workspace):
    proj = ws.project("acme")
    assert render_status(ws, proj, "demo")[0] == "missing"
    r = render_video(ws, proj, "demo", preview=True)
    info = probe(r.video)
    streams = info["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]          # no audio
    assert (streams[0]["width"], streams[0]["height"]) == (540, 540)
    rv, _ = ws.resolve(proj, "demo")
    assert float(info["format"]["duration"]) == pytest.approx(rv.total_duration, abs=0.1)

    # full render writes metadata + thumbnail and is "rendered"
    r = render_video(ws, proj, "demo")
    assert r.thumbnail.is_file()
    meta = json.loads(r.info.read_text())
    assert [s["id"] for s in meta["scenes"]] == ["hook", "points", "pic", "follow"]
    assert render_status(ws, proj, "demo")[0] == "rendered"

    # second render hits the cache for every scene
    r2 = render_video(ws, proj, "demo")
    assert r2.rendered == [] and len(r2.cached) == 4

    # editing one scene re-renders only that scene and marks the video stale meanwhile
    p = proj.video_path("demo")
    p.write_text(p.read_text().replace("A picture", "A new picture"))
    assert render_status(ws, proj, "demo")[0] == "stale"
    r3 = render_video(ws, proj, "demo")
    assert r3.rendered == ["pic"]


@needs_ffmpeg
def test_render_mixed_hard_cuts_and_xfades(ws: Workspace):
    """concat (hard cut) followed by xfade needs matching timebases."""
    proj = ws.project("acme")
    p = proj.video_path("demo")
    p.write_text(p.read_text().replace("layout: bullets", "layout: bullets\n  transition: none"))
    rv, _ = ws.resolve(proj, "demo")
    assert rv.scenes[1].transition.type == "none"
    r = render_video(ws, proj, "demo", preview=True)
    assert r.duration == pytest.approx(rv.total_duration, abs=0.1)
