"""`explainer` command-line interface."""

from __future__ import annotations

import json
import re
import shutil
from importlib import resources
from pathlib import Path
from typing import Optional

import typer
import yaml
from PIL import Image

from .loader import LoadError, Workspace, render_status
from .schema import FORMAT_SIZES, video_json_schema

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Generate short slideshow-style explainer videos from YAML scripts.")
PATTERNS = Path(str(resources.files("explainer") / "patterns"))

ProjectsDir = typer.Option(None, "--projects-dir", "-P", envvar="EXPLAINER_PROJECTS",
                           help="Directory containing projects (default: ./projects)")


def _ws(projects_dir: Optional[Path]) -> Workspace:
    return Workspace(projects_dir)


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, fg="red", err=True)
    raise typer.Exit(code)


def _project(ws: Workspace, name: str):
    try:
        return ws.project(name)
    except LoadError as e:
        _fail(str(e))


def _print_warnings(warnings: list[str]) -> None:
    for w in dict.fromkeys(warnings):
        typer.secho(f"  warning: {w}", fg="yellow")


def pattern_info() -> dict[str, dict[str, str]]:
    out = {}
    for p in sorted(PATTERNS.glob("*.yaml")):
        meta = {}
        for line in p.read_text().splitlines():
            m = re.match(r"#\s*(\w+):\s*(.*)", line)
            if m:
                meta[m.group(1)] = m.group(2)
            elif not line.startswith("#"):
                break
        out[p.stem] = {"path": str(p), **meta}
    return out


# --------------------------------------------------------------------------- scaffolding


@app.command()
def init(name: str = typer.Argument(..., help="Project directory name (kebab-case)"),
         title: Optional[str] = typer.Option(None, help="Display name"),
         template: str = typer.Option("bold-vertical", "--template", "-t"),
         format: str = typer.Option("vertical", "--format", "-f", help="square | vertical | portrait"),
         handle: Optional[str] = typer.Option(None, help="Social handle shown as watermark"),
         projects_dir: Optional[Path] = ProjectsDir):
    """Create a new project skeleton."""
    ws = _ws(projects_dir)
    if format not in FORMAT_SIZES:
        _fail(f"unknown format {format!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        _fail("project name must be lowercase letters, digits, '-' or '_'")
    root = ws.root / name
    if root.exists():
        _fail(f"{root} already exists")
    if template not in ws.template_paths():
        _fail(f"unknown template {template!r}")
    for d in ("assets", "videos", "templates", "output"):
        (root / d).mkdir(parents=True)
    cfg = {"name": title or name.replace("-", " ").title(), "description": "", "template": template, "format": format}
    if handle:
        cfg["branding"] = {"handle": handle}
    (root / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    typer.secho(f"created project {name} at {root}", fg="green")


@app.command()
def new(project: str, slug: str = typer.Argument(..., help="Video file name without .yaml (kebab-case)"),
        title: Optional[str] = typer.Option(None, help="Video title"),
        pattern: str = typer.Option("hook-points-cta", "--pattern", "-p", help="Scaffold pattern (see `explainer patterns`)"),
        template: Optional[str] = typer.Option(None, "--template", "-t"),
        format: Optional[str] = typer.Option(None, "--format", "-f"),
        force: bool = typer.Option(False, help="Overwrite an existing file"),
        projects_dir: Optional[Path] = ProjectsDir):
    """Scaffold a new video script from a pattern."""
    ws = _ws(projects_dir)
    proj = _project(ws, project)
    patterns = pattern_info()
    if pattern not in patterns:
        _fail(f"unknown pattern {pattern!r} (available: {', '.join(patterns)})")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        _fail("slug must be lowercase letters, digits, '-' or '_'")
    dest = proj.video_path(slug)
    if dest.exists() and not force:
        _fail(f"{dest} exists (use --force to overwrite)")
    text = Path(patterns[pattern]["path"]).read_text()
    text = "\n".join(l for l in text.splitlines() if not l.startswith("# ")) + "\n"
    head = "# yaml-language-server: $schema=../../../schema/video.schema.json\n"
    text = text.replace('title: "TITLE"', f"title: {json.dumps(title or slug.replace('-', ' ').capitalize())}", 1)
    extra = "".join(f"{k}: {v}\n" for k, v in (("template", template), ("format", format)) if v)
    text = text.replace("version: 1\n", "version: 1\n" + extra, 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(head + text)
    typer.secho(f"created {dest}", fg="green")


# --------------------------------------------------------------------------- listing


@app.command("list")
def list_(project: Optional[str] = typer.Argument(None), as_json: bool = typer.Option(False, "--json"),
          projects_dir: Optional[Path] = ProjectsDir):
    """List projects and their videos with render status."""
    ws = _ws(projects_dir)
    projects = [_project(ws, project)] if project else ws.projects()
    data = []
    for p in projects:
        vids = []
        for slug in p.video_slugs():
            status, info = render_status(ws, p, slug)
            vids.append({"slug": slug, "status": status, "duration": info and info.get("duration")})
        data.append({"project": p.slug, "name": p.meta.name, "template": p.meta.template, "videos": vids})
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    if not data:
        typer.echo(f"no projects in {ws.root} (create one with `explainer init NAME`)")
    colors = {"rendered": "green", "stale": "yellow", "missing": "white", "invalid": "red"}
    for p in data:
        typer.secho(f"{p['project']}  ({p['name']}, template {p['template']})", bold=True)
        for v in p["videos"]:
            dur = f"{v['duration']:.1f}s" if v["duration"] else ""
            typer.echo(f"  {v['slug']:<28} " + typer.style(f"{v['status']:<9}", fg=colors[v["status"]]) + f" {dur}")


@app.command()
def templates(project: Optional[str] = typer.Option(None, "--project", "-p"), projects_dir: Optional[Path] = ProjectsDir):
    """List available templates (built-in and project-local)."""
    ws = _ws(projects_dir)
    proj = _project(ws, project) if project else None
    for name, path in ws.template_paths(proj).items():
        try:
            t, _ = ws.template(name, proj)
        except LoadError as e:
            typer.secho(f"{name}: INVALID: {e}", fg="red")
            continue
        where = "project" if proj and path.is_relative_to(proj.path) else "built-in"
        typer.secho(f"{name}", bold=True, nl=False)
        typer.echo(f"  [{where}; formats: {', '.join(t.formats)}]")
        typer.echo(f"    {t.description}")
        c = t.theme.colors
        typer.echo(f"    colors: bg {c.background}, text {c.text}, accent {c.accent}; "
                   f"fonts: {t.theme.fonts.heading} / {t.theme.fonts.body}")


@app.command()
def patterns():
    """List narrative patterns usable with `explainer new --pattern`."""
    for name, meta in pattern_info().items():
        typer.secho(name, bold=True)
        for k in ("when", "shape", "length"):
            if k in meta:
                typer.echo(f"    {k}: {meta[k]}")


@app.command()
def assets(project: str, as_json: bool = typer.Option(False, "--json"), projects_dir: Optional[Path] = ProjectsDir):
    """List a project's assets with image dimensions and orientation."""
    ws = _ws(projects_dir)
    proj = _project(ws, project)
    rows = []
    for p in proj.assets():
        rel = str(p.relative_to(proj.path))
        row = {"path": rel, "bytes": p.stat().st_size}
        try:
            with Image.open(p) as im:
                w, h = im.size
                alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            row.update(width=w, height=h, alpha=alpha,
                       orientation="square" if 0.9 <= w / h <= 1.1 else ("landscape" if w > h else "portrait"))
        except Exception:
            row["kind"] = "other"
        rows.append(row)
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo(f"no assets in {proj.assets_dir}")
    for r in rows:
        dims = f"{r['width']}x{r['height']} {r['orientation']}{' (transparent)' if r.get('alpha') else ''}" if "width" in r else "(not an image)"
        typer.echo(f"  {r['path']:<40} {dims}")


# --------------------------------------------------------------------------- validate / render


def _targets(ws: Workspace, project: str, slug: Optional[str]):
    proj = _project(ws, project)
    slugs = [slug] if slug else proj.video_slugs()
    if slug and slug not in proj.video_slugs():
        _fail(f"video {slug!r} not found in project {project} ({proj.videos_dir})")
    if not slugs:
        _fail(f"project {project} has no videos (create one with `explainer new {project} SLUG`)")
    return proj, slugs


@app.command()
def validate(project: str, slug: Optional[str] = typer.Argument(None), strict: bool = typer.Option(False, help="Treat warnings as errors"),
             projects_dir: Optional[Path] = ProjectsDir):
    """Check schema, assets, readability and text fit. Exit 1 on errors."""
    from .render.pipeline import validate as run_validate

    ws = _ws(projects_dir)
    proj, slugs = _targets(ws, project, slug)
    failed = False
    for s in slugs:
        d = run_validate(ws, proj, s)
        bad = d.errors or (strict and d.warnings)
        typer.secho(f"{'FAIL' if bad else 'ok  '} {project}/{s}", fg="red" if bad else "green", bold=True)
        for e in d.errors:
            typer.secho("  " + e.replace("\n", "\n  "), fg="red")
        _print_warnings(d.warnings)
        failed |= bool(bad)
    raise typer.Exit(1 if failed else 0)


@app.command()
def still(project: str, slug: str, scene: Optional[list[str]] = typer.Option(None, "--scene", "-s", help="Scene id (repeatable)"),
          out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output directory"),
          projects_dir: Optional[Path] = ProjectsDir):
    """Render the final frame of each scene to PNG (plus sheet.png) for quick visual review."""
    from .render.pipeline import render_stills

    ws = _ws(projects_dir)
    proj, _ = _targets(ws, project, slug)
    try:
        paths, warnings = render_stills(ws, proj, slug, scene_ids=scene, out_dir=out)
    except LoadError as e:
        _fail(str(e))
    for p in paths:
        typer.echo(str(p))
    _print_warnings(warnings)


@app.command()
def render(project: str, slug: Optional[str] = typer.Argument(None, help="Video to render (default: all in project)"),
           preview: bool = typer.Option(False, help="Fast low-res render to <slug>.preview.mp4"),
           force: bool = typer.Option(False, help="Ignore the scene cache"),
           jobs: Optional[int] = typer.Option(None, "--jobs", "-j", help="Parallel scene renders"),
           projects_dir: Optional[Path] = ProjectsDir):
    """Render video(s) to MP4 in the project's output/ directory."""
    from .render.ffmpeg import FFmpegError
    from .render.pipeline import render_video

    ws = _ws(projects_dir)
    proj, slugs = _targets(ws, project, slug)
    failed = False
    for s in slugs:
        typer.secho(f"rendering {project}/{s}{' (preview)' if preview else ''}", bold=True)
        try:
            r = render_video(ws, proj, s, preview=preview, force=force, jobs=jobs, log=typer.echo)
        except (LoadError, FFmpegError) as e:
            typer.secho("  " + str(e).replace("\n", "\n  "), fg="red")
            failed = True
            continue
        typer.secho(f"  -> {r.video} ({r.duration:.1f}s, {len(r.rendered)} rendered, {len(r.cached)} cached, "
                    f"{r.seconds:.1f}s)", fg="green")
        _print_warnings(r.warnings)
    raise typer.Exit(1 if failed else 0)


@app.command()
def clean(project: str, days: float = typer.Option(14, help="Delete cached scene clips unused for this many days"),
          all_: bool = typer.Option(False, "--all", help="Delete the whole cache and stills"),
          projects_dir: Optional[Path] = ProjectsDir):
    """Prune the scene-clip cache."""
    from .render.pipeline import prune_cache

    ws = _ws(projects_dir)
    proj = _project(ws, project)
    if all_:
        for d in (proj.output_dir / ".cache", proj.output_dir / "stills"):
            shutil.rmtree(d, ignore_errors=True)
        typer.echo("cache and stills removed")
    else:
        typer.echo(f"removed {prune_cache(proj, days)} cached clip(s)")


# --------------------------------------------------------------------------- misc


@app.command()
def schema(out: Path = typer.Option(Path("schema/video.schema.json"), "--out", "-o")):
    """Write the JSON Schema for video YAML files (for editor autocompletion)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(video_json_schema(), indent=2) + "\n")
    typer.echo(f"wrote {out}")


@app.command()
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8000),
          projects_dir: Optional[Path] = ProjectsDir):
    """Start the read-only web UI."""
    import uvicorn

    from .web.app import create_app

    ws = _ws(projects_dir)
    typer.echo(f"serving {ws.root} on http://{host}:{port}")
    uvicorn.run(create_app(ws.root), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
