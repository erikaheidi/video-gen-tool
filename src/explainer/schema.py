"""Data model for projects, templates and video scripts.

Pydantic models are the single source of truth; `explainer schema` exports
them as JSON Schema for editor autocompletion.

Layering: every YAML layer (template, project, video, scene) is validated
into its own model, then merged with ``model_dump(exclude_unset=True)`` so
only the keys a layer actually sets override the layer beneath it.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- enums

Format = Literal["square", "vertical", "portrait"]
FORMAT_SIZES: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "vertical": (1080, 1920),
    "portrait": (1080, 1350),
}
BASE_WIDTH = 1080  # all pixel sizes in templates are expressed at this width

Layout = Literal[
    "title", "text", "bullets", "image", "image-caption", "split", "quote", "stat", "outro"
]
LAYOUTS: tuple[str, ...] = Layout.__args__  # type: ignore[attr-defined]

TransitionType = Literal[
    "none", "fade", "fadeblack", "slideleft", "slideright", "slideup", "slidedown",
    "wipeleft", "wiperight", "circleopen", "smoothleft", "smoothup", "dissolve",
]
Motion = Literal["none", "zoom-in", "zoom-out", "pan-left", "pan-right"]
TextAnimation = Literal["none", "fade-in", "stagger"]
Fit = Literal["cover", "contain"]
Align = Literal["left", "center", "right"]
VAlign = Literal["top", "middle", "bottom"]
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right", "bottom-center", "top-center"]

HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class Strict(BaseModel):
    """Base model: unknown keys are errors (catches typos in YAML)."""

    model_config = ConfigDict(extra="forbid")


def _check_color(v: str | None) -> str | None:
    if v is not None and not HEX_COLOR.match(v):
        raise ValueError(f"expected a hex colour like '#1e293b', got {v!r}")
    return v


# --------------------------------------------------------------------------- theme


class Colors(Strict):
    background: str = "#0f172a"
    text: str = "#f8fafc"
    accent: str = "#facc15"
    muted: str = "#94a3b8"
    panel: str = "#1e293b"

    @field_validator("*")
    @classmethod
    def _hex(cls, v: str) -> str:
        return _check_color(v)


class Fonts(Strict):
    heading: str = Field("NotoSans-ExtraBold", description="Font file name (without extension) or a project-relative path")
    body: str = "NotoSans-Medium"


class Sizes(Strict):
    """Font sizes in px at 1080px canvas width (scaled for other widths)."""

    title: int = 104
    subtitle: int = 54
    body: int = 60
    caption: int = 42
    stat: int = 280
    handle: int = 34


class Insets(Strict):
    top: int = 0
    bottom: int = 0
    left: int = 0
    right: int = 0


def _default_safe_areas() -> dict[str, Insets]:
    return {
        "vertical": Insets(top=200, bottom=340),
        "portrait": Insets(top=40, bottom=80),
        "square": Insets(),
    }


class Theme(Strict):
    colors: Colors = Field(default_factory=Colors)
    fonts: Fonts = Field(default_factory=Fonts)
    sizes: Sizes = Field(default_factory=Sizes)
    padding: int = Field(90, description="Inner margin in px at 1080 width")
    line_spacing: float = 1.18
    safe_area: dict[Format, Insets] = Field(
        default_factory=_default_safe_areas,
        description="Extra per-format insets keeping text clear of platform UI",
    )


# --------------------------------------------------------------------------- scene parts


class Transition(Strict):
    type: TransitionType = "fade"
    duration: float = Field(0.5, ge=0, le=2)


class ImageSpec(Strict):
    src: str = Field(description="Path relative to the project directory, e.g. assets/photo.jpg")
    fit: Fit = "cover"
    motion: Motion | None = None
    focus: tuple[float, float] = Field((0.5, 0.5), description="Crop focus point (x, y) in 0..1")


class Background(Strict):
    color: str | None = None
    image: str | None = None
    overlay: float = Field(0.0, ge=0, le=1, description="Darkening tint over the image (0..1)")
    overlay_color: str | None = None
    motion: Motion = "none"
    focus: tuple[float, float] = (0.5, 0.5)

    @field_validator("color", "overlay_color")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        return _check_color(v)


class LayoutStyle(Strict):
    """Visual knobs per layout; set in templates (`layouts:`) or per scene (`style:`)."""

    align: Align = "center"
    valign: VAlign = "middle"
    bullet: str = "•"
    stagger: float = Field(0.45, ge=0, description="Delay between staggered elements (s)")
    title_scale: float = 1.0
    body_scale: float = 1.0
    uppercase_title: bool = False
    panel: bool = Field(False, description="Draw a translucent panel behind the text")
    panel_opacity: float = Field(0.75, ge=0, le=1)
    accent_bar: bool = Field(False, description="Draw a short accent bar above the title")
    text_color: str | None = None
    title_color: str | None = None


class Branding(Strict):
    handle: str | None = None
    logo: str | None = Field(None, description="Project-relative path to a logo image")
    position: Corner = "bottom-center"
    opacity: float = Field(0.85, ge=0, le=1)
    show_on_scenes: bool = Field(True, description="Show the watermark on every scene (not just the outro)")


class Defaults(Strict):
    duration: float = Field(4.0, gt=0)
    transition: Transition = Field(default_factory=Transition)
    text_animation: TextAnimation = "fade-in"


# --------------------------------------------------------------------------- scene

_REQUIRED: dict[str, list[tuple[str, ...]]] = {
    # each tuple is "at least one of"
    "title": [("title",)],
    "text": [("body",)],
    "bullets": [("bullets",)],
    "image": [("image",)],
    "image-caption": [("image",), ("title", "caption")],
    "split": [("image",), ("title", "body")],
    "quote": [("quote",)],
    "stat": [("value",)],
    "outro": [("title",)],
}

TEXT_FIELDS = ("title", "subtitle", "body", "quote", "author", "value", "caption")


class Scene(Strict):
    id: str | None = Field(None, description="Stable kebab-case identifier (defaults to scene-N)")
    layout: Layout
    duration: float | None = Field(None, gt=0, le=30)
    title: str | None = None
    subtitle: str | None = None
    body: str | None = None
    bullets: list[str] | None = None
    quote: str | None = None
    author: str | None = None
    value: str | None = None
    caption: str | None = None
    image: ImageSpec | None = None
    background: Background | None = None
    transition: Transition | None = Field(None, description="Transition INTO this scene from the previous one")
    text_animation: TextAnimation | None = None
    style: LayoutStyle | None = None

    @field_validator("image", mode="before")
    @classmethod
    def _image_shorthand(cls, v: Any) -> Any:
        return {"src": v} if isinstance(v, str) else v

    @field_validator("background", mode="before")
    @classmethod
    def _background_shorthand(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"color": v} if v.startswith("#") else {"image": v}
        return v

    @field_validator("transition", mode="before")
    @classmethod
    def _transition_shorthand(cls, v: Any) -> Any:
        return {"type": v} if isinstance(v, str) else v

    @field_validator("id")
    @classmethod
    def _kebab(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", v):
            raise ValueError(f"scene id must be kebab-case (a-z, 0-9, '-'), got {v!r}")
        return v

    @model_validator(mode="after")
    def _layout_requirements(self) -> "Scene":
        for group in _REQUIRED[self.layout]:
            if not any(getattr(self, f) for f in group):
                raise ValueError(f"layout '{self.layout}' requires {' or '.join(group)}")
        return self


# --------------------------------------------------------------------------- files


class Template(Strict):
    name: str
    description: str = ""
    extends: str | None = Field(None, description="Name of a template to inherit from")
    formats: list[Format] = Field(default_factory=lambda: ["vertical", "square", "portrait"])
    theme: Theme = Field(default_factory=Theme)
    defaults: Defaults = Field(default_factory=Defaults)
    layouts: dict[Layout, LayoutStyle] = Field(default_factory=dict)
    branding: Branding = Field(default_factory=Branding)
    intro: list[Scene] = Field(default_factory=list, description="Scenes prepended to every video")
    outro: list[Scene] = Field(default_factory=list, description="Scenes appended to every video")


class Project(Strict):
    name: str
    description: str = ""
    template: str = "bold-vertical"
    format: Format | None = None
    branding: Branding | None = None
    theme: Theme | None = None
    tags: list[str] = Field(default_factory=list)


class Video(Strict):
    version: Literal[1] = 1
    title: str
    description: str = ""
    template: str | None = None
    format: Format | None = None
    fps: int = Field(30, ge=12, le=60)
    theme: Theme | None = None
    defaults: Defaults | None = None
    layouts: dict[Layout, LayoutStyle] | None = None
    branding: Branding | None = None
    intro: bool = Field(True, description="Include the template's intro scenes")
    outro: bool = Field(True, description="Include the template's outro scenes")
    scenes: list[Scene] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "Video":
        ids = [s.id for s in self.scenes if s.id]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate scene ids: {sorted(dupes)}")
        return self


# --------------------------------------------------------------------------- resolved


class ResolvedScene(BaseModel):
    """A scene with every inherited value filled in; what the renderer consumes."""

    id: str
    index: int
    layout: Layout
    duration: float
    title: str | None = None
    subtitle: str | None = None
    body: str | None = None
    bullets: list[str] | None = None
    quote: str | None = None
    author: str | None = None
    value: str | None = None
    caption: str | None = None
    image: ImageSpec | None = None
    background: Background
    transition: Transition
    text_animation: TextAnimation
    style: LayoutStyle
    origin: Literal["intro", "video", "outro"] = "video"


class ResolvedVideo(BaseModel):
    slug: str
    title: str
    description: str
    project: str
    template: str
    format: Format
    width: int
    height: int
    fps: int
    theme: Theme
    branding: Branding
    scenes: list[ResolvedScene]

    @property
    def scale(self) -> float:
        return self.width / BASE_WIDTH

    def timeline(self) -> list[tuple[float, float]]:
        """(start, end) of each scene in the final video, accounting for overlaps."""
        out, t = [], 0.0
        for i, s in enumerate(self.scenes):
            overlap = effective_overlap(self.scenes, i)
            start = t - overlap
            out.append((start, start + s.duration))
            t = start + s.duration
        return out

    @property
    def total_duration(self) -> float:
        tl = self.timeline()
        return tl[-1][1] if tl else 0.0


def effective_overlap(scenes: list[ResolvedScene], i: int) -> float:
    """Seconds by which scene i overlaps the previous scene (its transition)."""
    if i == 0:
        return 0.0
    tr = scenes[i].transition
    if tr.type == "none" or tr.duration <= 0:
        return 0.0
    # never overlap more than ~half of either neighbouring scene
    return round(min(tr.duration, scenes[i - 1].duration / 2, scenes[i].duration / 2), 3)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dicts; lists and scalars in ``override`` replace ``base``."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def video_json_schema() -> dict[str, Any]:
    schema = Video.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Explainer video script"
    return schema
