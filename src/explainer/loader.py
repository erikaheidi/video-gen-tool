"""Project discovery, YAML loading, template inheritance and layer merging."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import (
    FORMAT_SIZES,
    Background,
    Branding,
    Defaults,
    LayoutStyle,
    Project,
    ResolvedScene,
    ResolvedVideo,
    Scene,
    Template,
    Theme,
    Video,
    deep_merge,
)

BUILTIN_TEMPLATES = Path(str(resources.files("explainer") / "templates"))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class LoadError(Exception):
    """A user-facing problem with a YAML file (bad syntax, schema, missing file)."""


def default_projects_dir() -> Path:
    return Path(os.environ.get("EXPLAINER_PROJECTS", "projects")).resolve()


# --------------------------------------------------------------------------- YAML helpers


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LoadError(f"{path}: file not found") from None
    except yaml.YAMLError as e:
        raise LoadError(f"{path}: invalid YAML: {e}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise LoadError(f"{path}: top level must be a mapping")
    return data


def format_validation_error(path: Path | str, err: ValidationError, data: dict | None = None) -> str:
    lines = [f"{path}: {err.error_count()} schema error(s)"]
    for e in err.errors():
        loc = _pretty_loc(e["loc"], data)
        msg = e["msg"].removeprefix("Value error, ")
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)


def _pretty_loc(loc: tuple, data: dict | None) -> str:
    parts: list[str] = []
    cursor: Any = data
    for p in loc:
        if isinstance(p, int):
            label = f"[{p}]"
            if isinstance(cursor, list) and p < len(cursor):
                cursor = cursor[p]
                if isinstance(cursor, dict) and cursor.get("id"):
                    label = f"[{p}:{cursor['id']}]"
            parts.append(label)
        else:
            # pydantic adds the union/tag name for some errors; skip internal tags
            if isinstance(cursor, dict):
                cursor = cursor.get(p)
            parts.append(("." if parts else "") + str(p))
    return "".join(parts) or "(root)"


def validate_model(model: type, data: dict, path: Path | str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise LoadError(format_validation_error(path, e, data)) from None


def _set(model) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True, mode="python") if model is not None else {}


# --------------------------------------------------------------------------- diagnostics


@dataclass
class Diagnostics:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "Diagnostics") -> None:
        self.errors += other.errors
        self.warnings += other.warnings


# --------------------------------------------------------------------------- workspace


@dataclass
class ProjectRef:
    slug: str
    path: Path
    meta: Project

    @property
    def videos_dir(self) -> Path:
        return self.path / "videos"

    @property
    def assets_dir(self) -> Path:
        return self.path / "assets"

    @property
    def output_dir(self) -> Path:
        return self.path / "output"

    @property
    def templates_dir(self) -> Path:
        return self.path / "templates"

    def video_slugs(self) -> list[str]:
        if not self.videos_dir.is_dir():
            return []
        return sorted(p.stem for p in self.videos_dir.glob("*.yaml"))

    def video_path(self, slug: str) -> Path:
        return self.videos_dir / f"{slug}.yaml"

    def assets(self) -> list[Path]:
        if not self.assets_dir.is_dir():
            return []
        return sorted(p for p in self.assets_dir.rglob("*") if p.is_file() and not p.name.startswith("."))

    def resolve_path(self, rel: str) -> Path:
        """Resolve a project-relative path, refusing anything outside the project."""
        p = (self.path / rel).resolve()
        if not p.is_relative_to(self.path.resolve()):
            raise LoadError(f"path {rel!r} escapes the project directory")
        return p


class Workspace:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).resolve() if root else default_projects_dir()

    # projects ---------------------------------------------------------------
    def project_slugs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "project.yaml").is_file())

    def projects(self) -> list[ProjectRef]:
        out = []
        for slug in self.project_slugs():
            try:
                out.append(self.project(slug))
            except LoadError:
                continue
        return out

    def project(self, slug: str) -> ProjectRef:
        if not SLUG_RE.match(slug):
            raise LoadError(f"invalid project name {slug!r}")
        path = self.root / slug
        cfg = path / "project.yaml"
        if not cfg.is_file():
            raise LoadError(f"project {slug!r} not found in {self.root}")
        meta = validate_model(Project, read_yaml(cfg), cfg)
        return ProjectRef(slug=slug, path=path.resolve(), meta=meta)

    # templates --------------------------------------------------------------
    def template_paths(self, project: ProjectRef | None = None) -> dict[str, Path]:
        found: dict[str, Path] = {p.stem: p for p in BUILTIN_TEMPLATES.glob("*.yaml")}
        if project and project.templates_dir.is_dir():
            found.update({p.stem: p for p in project.templates_dir.glob("*.yaml")})
        return dict(sorted(found.items()))

    def template(self, name: str, project: ProjectRef | None = None, _chain: tuple[str, ...] = ()) -> tuple[Template, list[Path]]:
        """Load a template, resolving ``extends``. Returns it and the files involved."""
        if name in _chain:
            raise LoadError(f"template inheritance cycle: {' -> '.join(_chain + (name,))}")
        paths = self.template_paths(project)
        if name not in paths:
            raise LoadError(f"template {name!r} not found (available: {', '.join(paths)})")
        path = paths[name]
        raw = read_yaml(path)
        child = validate_model(Template, raw, path)
        if not child.extends:
            return child, [path]
        parent, files = self.template(child.extends, project, _chain + (name,))
        merged = deep_merge(parent.model_dump(mode="python"), _set(child))
        merged["extends"] = None
        return validate_model(Template, merged, path), files + [path]

    # videos -----------------------------------------------------------------
    def load_video(self, project: ProjectRef, slug: str) -> Video:
        path = project.video_path(slug)
        return validate_model(Video, read_yaml(path), path)

    def resolve(self, project: ProjectRef | str, slug: str, *, width: int | None = None) -> tuple[ResolvedVideo, Diagnostics]:
        """Merge template -> project -> video -> scene into a ResolvedVideo.

        Raises LoadError for schema problems; returns non-fatal issues as Diagnostics.
        """
        if isinstance(project, str):
            project = self.project(project)
        video = self.load_video(project, slug)
        tname = video.template or project.meta.template
        template, _ = self.template(tname, project)
        diag = Diagnostics()

        theme = Theme.model_validate(
            deep_merge(deep_merge(template.theme.model_dump(), _set(project.meta.theme)), _set(video.theme))
        )
        defaults = Defaults.model_validate(deep_merge(template.defaults.model_dump(), _set(video.defaults)))
        branding = Branding.model_validate(
            deep_merge(deep_merge(template.branding.model_dump(), _set(project.meta.branding)), _set(video.branding))
        )
        layouts = {k: v.model_dump() for k, v in template.layouts.items()}
        for k, v in (video.layouts or {}).items():
            layouts[k] = deep_merge(layouts.get(k, {}), _set(v))

        fmt = video.format or project.meta.format or template.formats[0]
        if fmt not in template.formats:
            diag.warnings.append(f"template {tname!r} is not designed for format {fmt!r} (supports {template.formats})")
        w, h = FORMAT_SIZES[fmt]
        if width:
            h = int(round(h * width / w / 2) * 2)
            w = width

        context = {"handle": branding.handle or "", "title": video.title, "project": project.meta.name}
        sources: list[tuple[str, Scene]] = []
        if video.intro:
            sources += [("intro", s) for s in template.intro]
        sources += [("video", s) for s in video.scenes]
        if video.outro:
            sources += [("outro", s) for s in template.outro]

        scenes: list[ResolvedScene] = []
        counters = {"intro": 0, "video": 0, "outro": 0}
        used_ids: set[str] = set()
        for origin, s in sources:
            counters[origin] += 1
            sid = s.id or (f"scene-{counters[origin]}" if origin == "video" else f"{origin}-{counters[origin]}")
            if sid in used_ids:
                sid = f"{origin}-{sid}"
            used_ids.add(sid)
            scenes.append(self._resolve_scene(s, sid, len(scenes), origin, theme, defaults, layouts, context))

        rv = ResolvedVideo(
            slug=slug, title=video.title, description=video.description, project=project.slug,
            template=tname, format=fmt, width=w, height=h, fps=video.fps, theme=theme,
            branding=branding, scenes=scenes,
        )
        diag.extend(check_video(rv, project))
        return rv, diag

    @staticmethod
    def _resolve_scene(s: Scene, sid: str, index: int, origin: str, theme: Theme, defaults: Defaults,
                       layouts: dict[str, dict], context: dict[str, str]) -> ResolvedScene:
        data = s.model_dump(exclude_unset=True)
        for f in ("title", "subtitle", "body", "quote", "author", "value", "caption"):
            if data.get(f):
                data[f] = _fill(data[f], context)
        if data.get("bullets"):
            data["bullets"] = [_fill(b, context) for b in data["bullets"]]

        style = LayoutStyle.model_validate(deep_merge(layouts.get(s.layout, {}), _set(s.style)))
        transition = s.transition or defaults.transition

        bg = Background.model_validate(deep_merge({"color": theme.colors.background}, _set(s.background)))
        if s.layout == "image" and s.image is not None:
            # full-bleed image: the image *is* the background
            bg_data: dict[str, Any] = {
                "color": bg.color, "image": s.image.src, "focus": s.image.focus,
                "motion": s.image.motion or (s.background.motion if s.background and "motion" in _set(s.background) else "zoom-in"),
                "overlay": bg.overlay if s.background and "overlay" in _set(s.background) else 0.2,
                "overlay_color": bg.overlay_color,
            }
            bg = Background.model_validate(bg_data)

        data.update(
            id=sid, index=index, origin=origin,
            duration=s.duration or defaults.duration,
            background=bg, transition=transition,
            text_animation=s.text_animation or defaults.text_animation,
            style=style,
        )
        return ResolvedScene.model_validate(data)


def _fill(text: str, context: dict[str, str]) -> str:
    return re.sub(r"\{(handle|title|project)\}", lambda m: context[m.group(1)], text)


# --------------------------------------------------------------------------- checks


def check_video(video: ResolvedVideo, project: ProjectRef) -> Diagnostics:
    d = Diagnostics()

    def asset(rel: str | None, where: str) -> None:
        if not rel:
            return
        try:
            p = project.resolve_path(rel)
        except LoadError as e:
            d.errors.append(f"{where}: {e}")
            return
        if not p.is_file():
            d.errors.append(f"{where}: file not found: {rel}")
        elif p.suffix.lower() not in IMAGE_EXTS:
            d.errors.append(f"{where}: unsupported image type: {rel}")

    asset(video.branding.logo, "branding.logo")
    for s in video.scenes:
        where = f"scene {s.id}"
        asset(s.background.image, f"{where}.background.image")
        if s.image and s.layout != "image":
            asset(s.image.src, f"{where}.image")
        for b in s.bullets or []:
            if len(b.split()) > 6:
                d.warnings.append(f"{where}: bullet has {len(b.split())} words (aim for <= 6): {b!r}")
        if s.bullets and len(s.bullets) > 4:
            d.warnings.append(f"{where}: {len(s.bullets)} bullets (aim for <= 4)")
        words = sum(len((getattr(s, f) or "").split()) for f in ("title", "subtitle", "body", "quote", "caption"))
        words += sum(len(b.split()) for b in s.bullets or [])
        needed = 1.0 + words / 4.0  # ~240 wpm plus a beat to notice the scene
        if s.duration + 0.01 < needed and words > 4:
            d.warnings.append(f"{where}: {words} words in {s.duration:g}s may be too fast to read (suggest >= {needed:.1f}s)")
        if s.index > 0 and s.transition.type != "none" and s.transition.duration > min(s.duration, video.scenes[s.index - 1].duration) / 2:
            d.warnings.append(f"{where}: transition {s.transition.duration:g}s is long for its scenes; it will be shortened")

    total = video.total_duration
    if total < 5:
        d.warnings.append(f"total duration {total:.1f}s is very short (< 5s)")
    elif total > 90:
        d.warnings.append(f"total duration {total:.1f}s is long for social media (> 90s)")
    return d


# --------------------------------------------------------------------------- hashing / status


def file_fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def video_assets(video: ResolvedVideo, project: ProjectRef) -> list[Path]:
    rels = {video.branding.logo} if video.branding.logo else set()
    for s in video.scenes:
        rels.add(s.background.image)
        if s.image:
            rels.add(s.image.src)
    out = []
    for r in sorted(x for x in rels if x):
        try:
            p = project.resolve_path(r)
        except LoadError:
            continue
        if p.is_file():
            out.append(p)
    return out


def source_hash(video: ResolvedVideo, project: ProjectRef) -> str:
    """Fingerprint of everything that affects the rendered output."""
    payload = {
        "video": json.loads(video.model_dump_json()),
        "assets": {str(p.relative_to(project.path)): file_fingerprint(p) for p in video_assets(video, project)},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def render_status(ws: Workspace, project: ProjectRef, slug: str) -> tuple[str, dict | None]:
    """Return ("rendered" | "stale" | "missing" | "invalid", render-info)."""
    info_path = project.output_dir / f"{slug}.render.json"
    mp4 = project.output_dir / f"{slug}.mp4"
    info = None
    if info_path.is_file() and mp4.is_file():
        try:
            info = json.loads(info_path.read_text())
        except json.JSONDecodeError:
            info = None
    try:
        rv, _ = ws.resolve(project, slug)
    except LoadError:
        return "invalid", info
    if info is None:
        return "missing", None
    return ("rendered" if info.get("source_hash") == source_hash(rv, project) else "stale"), info
