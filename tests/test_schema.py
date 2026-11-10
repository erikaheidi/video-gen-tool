import pytest
import yaml
from pydantic import ValidationError

from explainer.loader import LoadError, Workspace
from explainer.schema import LAYOUTS, Scene, Video, deep_merge

from .conftest import write_yaml


def test_minimal_video_validates():
    v = Video.model_validate({"title": "x", "scenes": [{"layout": "title", "title": "Hi"}]})
    assert v.fps == 30 and v.scenes[0].duration is None


@pytest.mark.parametrize("scene, msg", [
    ({"layout": "bullets", "title": "x"}, "requires bullets"),
    ({"layout": "image-caption", "image": "a.png"}, "requires title or caption"),
    ({"layout": "stat"}, "requires value"),
    ({"layout": "title", "title": "x", "colour": "red"}, "Extra inputs"),
    ({"layout": "title", "title": "x", "id": "Not Kebab"}, "kebab-case"),
    ({"layout": "title", "title": "x", "background": {"color": "red"}}, "hex colour"),
    ({"layout": "nope", "title": "x"}, "layout"),
])
def test_scene_errors(scene, msg):
    with pytest.raises(ValidationError, match=msg):
        Scene.model_validate(scene)


def test_shorthands():
    s = Scene.model_validate({"layout": "image", "image": "assets/a.jpg", "background": "#112233", "transition": "wipeleft"})
    assert s.image.src == "assets/a.jpg" and s.background.color == "#112233" and s.transition.type == "wipeleft"


def test_duplicate_ids():
    with pytest.raises(ValidationError, match="duplicate"):
        Video.model_validate({"title": "x", "scenes": [{"id": "a", "layout": "title", "title": "1"},
                                                       {"id": "a", "layout": "title", "title": "2"}]})


def test_deep_merge_replaces_lists():
    assert deep_merge({"a": {"b": 1, "c": [1, 2]}, "d": 1}, {"a": {"c": [3]}}) == {"a": {"b": 1, "c": [3]}, "d": 1}


def test_merge_order(ws: Workspace):
    proj = ws.project("acme")
    write_yaml(proj.path / "project.yaml", {"name": "Acme", "template": "bold-vertical", "format": "square",
                                            "theme": {"colors": {"accent": "#ff0000"}, "padding": 50}})
    path = proj.video_path("demo")
    data = yaml.safe_load(path.read_text())
    data["theme"] = {"colors": {"accent": "#00ff00"}}
    data["defaults"] = {"duration": 7}
    data["scenes"][1].pop("duration")
    data["scenes"][1]["style"] = {"align": "right"}
    write_yaml(path, data)
    rv, _ = ws.resolve("acme", "demo")
    assert rv.theme.colors.accent == "#00ff00"        # video beats project
    assert rv.theme.padding == 50                     # project beats template
    assert rv.theme.colors.background == "#0f172a"    # template value kept
    assert rv.scenes[1].duration == 7                 # video defaults fill scene
    assert rv.scenes[1].style.align == "right"        # scene style beats template layout style
    assert rv.scenes[1].style.bullet == "→"           # template layout style kept
    assert rv.format == "square" and (rv.width, rv.height) == (1080, 1080)


def test_template_outro_and_placeholders(ws: Workspace):
    rv, _ = ws.resolve("acme", "demo")
    assert rv.scenes[-1].origin == "outro" and rv.scenes[-1].subtitle == "@acme"
    data = yaml.safe_load(ws.project("acme").video_path("demo").read_text())
    data["outro"] = False
    write_yaml(ws.project("acme").video_path("demo"), data)
    rv, _ = ws.resolve("acme", "demo")
    assert all(s.origin == "video" for s in rv.scenes)


def test_template_extends_and_project_templates(ws: Workspace):
    proj = ws.project("acme")
    write_yaml(proj.templates_dir / "mine.yaml", {"name": "mine", "extends": "dark-square",
                                                  "theme": {"colors": {"text": "#eeeeee"}}})
    t, files = ws.template("mine", proj)
    assert [f.stem for f in files] == ["bold-vertical", "dark-square", "mine"]
    assert t.theme.colors.text == "#eeeeee"            # child
    assert t.theme.colors.accent == "#22d3ee"          # parent
    assert t.theme.fonts.heading == "NotoSans-ExtraBold"  # grandparent
    assert t.outro and t.outro[0].layout == "outro"


def test_template_cycle(ws: Workspace):
    proj = ws.project("acme")
    write_yaml(proj.templates_dir / "a.yaml", {"name": "a", "extends": "b"})
    write_yaml(proj.templates_dir / "b.yaml", {"name": "b", "extends": "a"})
    with pytest.raises(LoadError, match="cycle"):
        ws.template("a", proj)


def test_missing_asset_is_error(ws: Workspace):
    path = ws.project("acme").video_path("demo")
    data = yaml.safe_load(path.read_text())
    data["scenes"][0]["background"]["image"] = "assets/missing.jpg"
    write_yaml(path, data)
    _, diag = ws.resolve("acme", "demo")
    assert any("missing.jpg" in e for e in diag.errors)


def test_asset_path_escape_is_error(ws: Workspace):
    path = ws.project("acme").video_path("demo")
    data = yaml.safe_load(path.read_text())
    data["scenes"][2]["image"] = "../../../etc/passwd"
    write_yaml(path, data)
    _, diag = ws.resolve("acme", "demo")
    assert any("escapes" in e for e in diag.errors)


def test_friendly_error_locations(ws: Workspace):
    path = ws.project("acme").video_path("demo")
    data = yaml.safe_load(path.read_text())
    data["scenes"][1]["bullets"] = None
    write_yaml(path, data)
    with pytest.raises(LoadError, match=r"scenes\[1:points\]"):
        ws.resolve("acme", "demo")


def test_timeline_accounts_for_overlaps(ws: Workspace):
    rv, _ = ws.resolve("acme", "demo")
    tl = rv.timeline()
    # hook 2s, points 2.5s (fade 0.45 overlap), pic 2s (slideleft 0.4), outro 3s (fadeblack 0.5)
    assert tl[0] == (0.0, 2.0)
    assert tl[1][0] == pytest.approx(2.0 - 0.45)
    assert rv.total_duration == pytest.approx(2 + 2.5 + 2 + 3 - 0.45 - 0.4 - 0.5)


def test_schema_reference_documents_every_layout():
    from pathlib import Path
    ref = Path(__file__).parents[1] / ".claude" / "skills" / "explainer-video" / "references" / "schema.md"
    text = ref.read_text()
    for layout in LAYOUTS:
        assert f"`{layout}`" in text, f"layout {layout} missing from {ref}"
