# explainer

Generate short, silent, slideshow-style explainer videos for social media (square, vertical 9:16, or portrait 4:5) from YAML scripts. Pillow lays out each scene; ffmpeg adds motion (Ken Burns zoom and pan, text fade-ins, staggered bullets) and transitions, and encodes the H.264 MP4.

```
projects/<project>/
  project.yaml        # name, default template/format, branding (handle, logo)
  assets/             # this project's images, logos, fonts
  templates/          # optional project templates (can `extends:` a built-in)
  videos/<slug>.yaml  # video scripts
  output/             # generated: <slug>.mp4, <slug>.jpg, <slug>.render.json, stills/, .cache/
```

## Setup
Requires Python 3.11+, ffmpeg (with libx264), and TTF fonts. The built-in templates use Noto Sans and Noto Serif.

```bash
# Wolfi: apk add python-3.12 py3.12-pip ffmpeg font-noto fontconfig
# macOS: brew install ffmpeg && brew install --cask font-noto-sans font-noto-serif
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Usage
```bash
explainer init my-project --template bold-vertical --format vertical --handle @me
explainer new my-project why-x --pattern hook-points-cta --title "Why X matters"
explainer validate my-project why-x      # schema, assets, readability, text-fit checks
explainer still my-project why-x         # PNG of every scene + sheet.png for quick review
explainer render my-project why-x        # -> projects/my-project/output/why-x.mp4
explainer render my-project --preview    # fast 540p render of every video
explainer list                           # all projects/videos with render status
explainer serve                          # read-only web UI at http://127.0.0.1:8000
```
Other commands: `templates`, `patterns`, `assets`, `schema`, `clean`. Every command accepts `--projects-dir` (or the `EXPLAINER_PROJECTS` environment variable). By default it reads `./projects`.

Scenes are cached by content hash, so a re-render only rebuilds the scenes that changed. A video is reported as **stale** when its script, template, or assets have changed since the last render.

## Writing scripts
```yaml
# yaml-language-server: $schema=../../../schema/video.schema.json
version: 1
title: What is an SBOM?
scenes:
  - id: hook
    layout: title
    duration: 3
    title: Do you know what's *inside* your containers?   # *word* = accent colour
    background: { image: assets/containers.jpg, overlay: 0.6, motion: zoom-in }
  - id: contents
    layout: bullets
    title: It tells you
    text_animation: stagger
    bullets: [Every package inside, The exact *versions*, Licenses and suppliers]
```

The layouts are `title`, `text`, `bullets`, `image`, `image-caption`, `split`, `quote`, `stat`, and `outro`. See `.claude/skills/explainer-video/references/schema.md` for every field. Templates (`src/explainer/templates/`) define the theme (colours, fonts, sizes, safe areas), defaults, per-layout styles, and intro and outro scenes. Values merge in this order: template → project → video → scene.

Narrative patterns (`explainer patterns`) are scaffolds for common story shapes: hook-points-cta, problem-solution, listicle, how-to, myth-vs-fact, stat-spotlight, and quote-card.

## Generating videos from a brief with Claude Code
The `explainer-video` skill (`.claude/skills/explainer-video/`) tells Claude how to turn a brief into a script, validate it, check the stills visually, and render it. For example: *"Make a 30s vertical video for the demo project on why SBOMs matter, CTA: add it to CI."*

## Tests
```bash
.venv/bin/pytest
```
