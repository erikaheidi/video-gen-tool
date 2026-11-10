"""End-to-end rendering: resolve -> scene layers -> cached scene clips -> joined MP4."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from ..loader import Diagnostics, LoadError, ProjectRef, Workspace, file_fingerprint, source_hash
from ..schema import ResolvedScene, ResolvedVideo, effective_overlap
from . import ffmpeg
from .scene import SceneRenderer

RENDERER_VERSION = "1"


def _code_fingerprint() -> str:
    """Hash of the renderer's own source, so upgrading the renderer invalidates cached clips."""
    h = hashlib.sha256(RENDERER_VERSION.encode())
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


CODE_FINGERPRINT = _code_fingerprint()
PREVIEW_WIDTH = 540

Log = Callable[[str], None]


@dataclass
class RenderResult:
    video: Path
    thumbnail: Path | None
    info: Path | None
    duration: float
    warnings: list[str] = field(default_factory=list)
    rendered: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    seconds: float = 0.0


# --------------------------------------------------------------------------- validation


def validate(ws: Workspace, project: ProjectRef, slug: str) -> Diagnostics:
    """Schema + asset checks + layout (text fit) checks. Never raises for user errors."""
    try:
        rv, diag = ws.resolve(project, slug)
    except LoadError as e:
        return Diagnostics(errors=[str(e)])
    if diag.ok:
        renderer = SceneRenderer(rv, project)
        for s in rv.scenes:
            try:
                diag.warnings += renderer.check(s)
            except (OSError, LoadError) as e:
                diag.errors.append(f"scene {s.id}: {e}")
    return diag


# --------------------------------------------------------------------------- stills


def render_stills(ws: Workspace, project: ProjectRef, slug: str, *, scene_ids: list[str] | None = None,
                  out_dir: Path | None = None, sheet: bool = True) -> tuple[list[Path], list[str]]:
    """Write the final-state frame of each scene as PNG (plus a contact sheet)."""
    rv, diag = ws.resolve(project, slug)
    if diag.errors:
        raise LoadError("\n".join(diag.errors))
    out_dir = out_dir or project.output_dir / "stills" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    renderer = SceneRenderer(rv, project)
    paths, warnings, thumbs = [], list(diag.warnings), []
    for s in rv.scenes:
        if scene_ids and s.id not in scene_ids:
            continue
        img, warn = renderer.still(s)
        warnings += warn
        p = out_dir / f"{s.index + 1:02d}-{s.id}.png"
        img.save(p)
        paths.append(p)
        thumbs.append((s, img))
    if sheet and len(thumbs) > 1:
        p = out_dir / "sheet.png"
        contact_sheet(thumbs).save(p)
        paths.append(p)
    return paths, warnings


def contact_sheet(items: list[tuple[ResolvedScene, Image.Image]], cols: int = 5, thumb_w: int = 300) -> Image.Image:
    w0, h0 = items[0][1].size
    tw, th = thumb_w, int(thumb_w * h0 / w0)
    label_h = 34
    rows = (len(items) + cols - 1) // cols
    cols = min(cols, len(items))
    sheet = Image.new("RGB", (cols * (tw + 12) + 12, rows * (th + label_h + 12) + 12), "#e5e7eb")
    d = ImageDraw.Draw(sheet)
    for i, (s, img) in enumerate(items):
        x = 12 + (i % cols) * (tw + 12)
        y = 12 + (i // cols) * (th + label_h + 12)
        sheet.paste(img.resize((tw, th), Image.LANCZOS), (x, y + label_h))
        d.text((x + 2, y + 8), f"{s.index + 1}. {s.id} [{s.layout}] {s.duration:g}s", fill="#111827")
    return sheet


# --------------------------------------------------------------------------- video


def scene_key(rv: ResolvedVideo, project: ProjectRef, s: ResolvedScene, enc: ffmpeg.Encoding) -> str:
    assets = [a for a in (s.background.image, s.image.src if s.image else None, rv.branding.logo) if a]
    payload = {
        "v": CODE_FINGERPRINT,
        "scene": json.loads(s.model_dump_json(exclude={"index", "origin"})),
        "first": s.index == 0,
        "overlap": effective_overlap(rv.scenes, s.index),
        "theme": json.loads(rv.theme.model_dump_json()),
        "branding": json.loads(rv.branding.model_dump_json()),
        "size": [rv.width, rv.height, rv.fps, rv.format],
        "enc": [enc.preset, enc.crf],
        "assets": {a: file_fingerprint(project.resolve_path(a)) for a in sorted(set(assets))},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def render_video(ws: Workspace, project: ProjectRef, slug: str, *, preview: bool = False, force: bool = False,
                 jobs: int | None = None, log: Log = lambda _: None) -> RenderResult:
    t0 = time.monotonic()
    rv, diag = ws.resolve(project, slug, width=PREVIEW_WIDTH if preview else None)
    if diag.errors:
        raise LoadError("\n".join(diag.errors))
    out_dir = project.output_dir
    cache = out_dir / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    enc_i, enc_f = ffmpeg.Encoding.intermediate(preview), ffmpeg.Encoding.final(preview)
    result_warnings = list(diag.warnings)
    rendered, cached = [], []

    def build(s: ResolvedScene) -> tuple[Path, list[str]]:
        renderer = SceneRenderer(rv, project)  # one per task: renderers hold per-scene state
        key = scene_key(rv, project, s, enc_i)
        clip = cache / f"{key}.mp4"
        if clip.is_file() and not force:
            clip.touch()  # keep recently used clips out of prune_cache
            cached.append(s.id)
            return clip, renderer.check(s)
        with tempfile.TemporaryDirectory(prefix=f"explainer-{s.id}-") as tmp:
            layers = renderer.render(s, Path(tmp))
            part = cache / f"{key}.part.mp4"
            ffmpeg.run(ffmpeg.scene_clip_cmd(layers, part, width=rv.width, height=rv.height, fps=rv.fps, enc=enc_i))
            part.replace(clip)
        rendered.append(s.id)
        log(f"  rendered scene {s.index + 1}/{len(rv.scenes)}: {s.id}")
        return clip, layers.warnings

    jobs = jobs or max(1, min(4, (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        built = list(pool.map(build, rv.scenes))
    for _, w in built:
        result_warnings += w

    clips = [ffmpeg.JoinSpec(path=c, duration=s.duration, transition=s.transition.type,
                             overlap=effective_overlap(rv.scenes, s.index))
             for (c, _), s in zip(built, rv.scenes)]
    suffix = ".preview" if preview else ""
    final = out_dir / f"{slug}{suffix}.mp4"
    part = out_dir / f".{slug}{suffix}.part.mp4"
    log(f"  joining {len(clips)} scenes -> {final.name}")
    if len(clips) == 1:
        shutil.copyfile(clips[0].path, part)
    else:
        ffmpeg.run(ffmpeg.join_cmd(clips, part, fps=rv.fps, enc=enc_f))
    part.replace(final)
    duration = float(ffmpeg.probe(final)["format"]["duration"])

    thumb = info_path = None
    if not preview:
        thumb = out_dir / f"{slug}.jpg"
        ffmpeg.run(ffmpeg.thumbnail_cmd(final, thumb, at=min(1.0, rv.scenes[0].duration * 0.5)))
        info_path = out_dir / f"{slug}.render.json"
        timeline = rv.timeline()
        info = {
            "slug": slug,
            "title": rv.title,
            "source_hash": source_hash(ws.resolve(project, slug)[0], project),
            "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration": round(duration, 3),
            "width": rv.width, "height": rv.height, "fps": rv.fps,
            "format": rv.format, "template": rv.template,
            "scenes": [{"id": s.id, "layout": s.layout, "origin": s.origin, "start": round(a, 3), "end": round(b, 3)}
                       for s, (a, b) in zip(rv.scenes, timeline)],
            "warnings": _dedupe(result_warnings),
            "renderer_version": RENDERER_VERSION,
        }
        info_path.write_text(json.dumps(info, indent=2))

    return RenderResult(final, thumb, info_path, duration, _dedupe(result_warnings), rendered, cached,
                        time.monotonic() - t0)


def prune_cache(project: ProjectRef, max_age_days: float = 14) -> int:
    cache = project.output_dir / ".cache"
    if not cache.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    n = 0
    for p in cache.glob("*.mp4"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            n += 1
    return n


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
