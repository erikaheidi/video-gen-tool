"""Read-only web UI for browsing projects, scripts and rendered videos.

Every route is GET-only and nothing here writes to disk.
"""

from __future__ import annotations

import json
from datetime import datetime
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..loader import IMAGE_EXTS, LoadError, ProjectRef, Workspace, render_status
from ..render.pipeline import validate

HERE = Path(str(resources.files("explainer") / "web"))
MEDIA_KINDS = {"output", "assets"}


def create_app(root: Path | str | None = None) -> FastAPI:
    ws = Workspace(root)
    app = FastAPI(title="explainer", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["dt"] = _fmt_dt
    templates.env.filters["secs"] = lambda v: f"{v:.1f}s" if isinstance(v, (int, float)) else "—"

    def project_or_404(name: str) -> ProjectRef:
        try:
            return ws.project(name)
        except LoadError:
            raise HTTPException(404, "project not found") from None

    def media_url(p: ProjectRef, kind: str, path: Path) -> str:
        return f"/media/{p.slug}/{kind}/{path.relative_to(p.path / kind).as_posix()}"

    def video_card(p: ProjectRef, slug: str) -> dict:
        status, info = render_status(ws, p, slug)
        title, fmt, n_scenes = slug, None, None
        try:
            v = ws.load_video(p, slug)
            title, fmt, n_scenes = v.title, v.format or p.meta.format, len(v.scenes)
        except LoadError:
            pass
        thumb = p.output_dir / f"{slug}.jpg"
        return {
            "slug": slug, "title": title, "status": status, "info": info, "scenes": n_scenes,
            "format": (info or {}).get("format") or fmt or "vertical",
            "thumb": media_url(p, "output", thumb) if thumb.is_file() and info else None,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        projects = []
        for p in ws.projects():
            cards = [video_card(p, s) for s in p.video_slugs()]
            rendered = [c for c in cards if c["info"]]
            latest = max(rendered, key=lambda c: c["info"].get("rendered_at", ""), default=None)
            projects.append({"ref": p, "videos": cards, "n_rendered": len(rendered),
                             "thumb": latest["thumb"] if latest else None, "n_assets": len(p.assets())})
        return templates.TemplateResponse(request, "index.html", {"projects": projects, "root": ws.root})

    @app.get("/p/{project}", response_class=HTMLResponse)
    def project_page(request: Request, project: str):
        p = project_or_404(project)
        cards = [video_card(p, s) for s in p.video_slugs()]
        assets = [{"name": a.relative_to(p.assets_dir).as_posix(), "url": media_url(p, "assets", a),
                   "image": a.suffix.lower() in IMAGE_EXTS} for a in p.assets()]
        return templates.TemplateResponse(request, "project.html", {"p": p, "videos": cards, "assets": assets})

    @app.get("/p/{project}/v/{slug}", response_class=HTMLResponse)
    def video_page(request: Request, project: str, slug: str):
        p = project_or_404(project)
        if slug not in p.video_slugs():
            raise HTTPException(404, "video not found")
        card = video_card(p, slug)
        source = p.video_path(slug).read_text(encoding="utf-8")
        diag = validate(ws, p, slug)
        scenes, resolved = [], None
        try:
            resolved, _ = ws.resolve(p, slug)
            for s, (start, end) in zip(resolved.scenes, resolved.timeline()):
                text = [x for x in (s.title, s.subtitle, s.body, s.quote, s.value, s.caption) if x]
                text += [f"• {b}" for b in s.bullets or []]
                scenes.append({"id": s.id, "layout": s.layout, "origin": s.origin, "start": start, "end": end,
                               "duration": s.duration, "text": [t.replace("*", "") for t in text],
                               "transition": s.transition.type if s.index else ""})
        except LoadError:
            pass
        mp4 = p.output_dir / f"{slug}.mp4"
        preview = p.output_dir / f"{slug}.preview.mp4"
        stills_dir = p.output_dir / "stills" / slug
        stills = [media_url(p, "output", x) for x in sorted(stills_dir.glob("*.png")) if x.name != "sheet.png"] \
            if stills_dir.is_dir() else []
        ctx = {
            "p": p, "v": card, "source": source, "diag": diag, "scenes": scenes, "resolved": resolved,
            "video_url": media_url(p, "output", mp4) if mp4.is_file() else None,
            "preview_url": media_url(p, "output", preview) if preview.is_file() else None,
            "stills": stills,
        }
        return templates.TemplateResponse(request, "video.html", ctx)

    @app.get("/p/{project}/v/{slug}/render.json")
    def video_json(project: str, slug: str):
        p = project_or_404(project)
        info = p.output_dir / f"{slug}.render.json"
        if slug not in p.video_slugs() or not info.is_file():
            raise HTTPException(404, "not rendered")
        return json.loads(info.read_text())

    @app.get("/media/{project}/{kind}/{path:path}")
    def media(project: str, kind: str, path: str):
        p = project_or_404(project)
        if kind not in MEDIA_KINDS:
            raise HTTPException(404)
        base = (p.path / kind).resolve()
        target = (base / path).resolve()
        rel_parts = target.relative_to(base).parts if target.is_relative_to(base) else None
        if not rel_parts or any(part.startswith(".") for part in rel_parts) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    return app


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value
