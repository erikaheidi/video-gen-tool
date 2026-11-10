import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image

from explainer.loader import Workspace


def write_yaml(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """A workspace with one project 'acme' containing an image asset and a 3-scene video."""
    root = tmp_path / "projects"
    proj = root / "acme"
    (proj / "assets").mkdir(parents=True)
    Image.new("RGB", (800, 600), "#336699").save(proj / "assets" / "photo.jpg")
    Image.new("RGBA", (200, 200), (255, 200, 0, 255)).save(proj / "assets" / "logo.png")
    write_yaml(proj / "project.yaml", {
        "name": "Acme", "template": "bold-vertical", "format": "square",
        "branding": {"handle": "@acme", "logo": "assets/logo.png"},
    })
    write_yaml(proj / "videos" / "demo.yaml", {
        "title": "Demo video",
        "scenes": [
            {"id": "hook", "layout": "title", "duration": 2, "title": "Hello *world*",
             "background": {"image": "assets/photo.jpg", "overlay": 0.5, "motion": "zoom-in"}},
            {"id": "points", "layout": "bullets", "duration": 2.5, "title": "Points",
             "bullets": ["One", "Two"], "text_animation": "stagger"},
            {"id": "pic", "layout": "image-caption", "duration": 2, "image": "assets/photo.jpg",
             "title": "A picture", "transition": {"type": "slideleft", "duration": 0.4}},
        ],
    })
    return Workspace(root)


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
