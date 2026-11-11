# CLAUDE.md

`explainer`: a Python CLI and read-only web UI that turns YAML scripts into short, silent, slideshow-style social videos (square 1080x1080, vertical 1080x1920, portrait 1080x1350). Pillow draws each scene. ffmpeg adds motion and transitions and encodes H.264 MP4.

User-facing docs: [README.md](README.md). Full YAML field reference: [.claude/skills/explainer-video/references/schema.md](.claude/skills/explainer-video/references/schema.md).

## Commands
Run these from the repo root. The CLI reads `./projects`, or `--projects-dir` / `EXPLAINER_PROJECTS` if set.
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # needs Python 3.11+, ffmpeg+libx264, Noto fonts
.venv/bin/pytest                                             # ffmpeg tests skip if ffmpeg/ffprobe are missing
.venv/bin/explainer validate <project> [slug] [--strict]
.venv/bin/explainer still <project> <slug>                   # PNG stills + sheet.png in output/stills/<slug>/
.venv/bin/explainer render <project> [slug] [--preview] [--force]
.venv/bin/explainer schema                                   # regenerates schema/video.schema.json
.venv/bin/explainer serve                                    # web UI on 127.0.0.1:8000
```
Other subcommands: `init`, `new`, `list`, `templates`, `patterns`, `assets`, `clean`.

## Video requests
When the user gives a brief or asks for a video, reel, or short, or asks to edit a script YAML, use the **`explainer-video` skill** ([.claude/skills/explainer-video/SKILL.md](.claude/skills/explainer-video/SKILL.md)). It covers the workflow: brief → pattern → YAML → validate → stills QA → render. Copy rules are in its `references/patterns.md`.

## Architecture (`src/explainer/`)
- `schema.py`: Pydantic models. This is the **source of truth** for the YAML format. `Strict` base = `extra="forbid"`, so unknown keys are errors. Covers Theme, LayoutStyle, Scene, Template, Project, Video, and the `Resolved*` models; also `deep_merge` and `video_json_schema`.
- `loader.py`: `Workspace` (finds projects and templates, plus `resolve()`), `ProjectRef`, `Diagnostics` (errors/warnings), `check_video`, and source hashing / `render_status` (fresh/stale).
  - Merge order (later wins): template (`extends:` chain) → `project.yaml` → video → scene. Dicts deep-merge, lists replace.
  - Templates: built-ins in `src/explainer/templates/`. Project templates in `projects/<p>/templates/` override them by name.
- `render/layout.py`: font lookup (`find_font`), `*accent*` rich-text parsing, wrapping/balancing, auto-fit (`layout_text`), glyph checks.
- `render/scene.py`: `SceneRenderer` renders one scene into `SceneLayers`: a background PNG with motion, an optional chrome layer (branding), and foreground layers with fade timings. It also handles the contrast auto-adjust.
- `render/ffmpeg.py`: command builders: motion (Ken Burns) filters, per-scene clip, xfade join, thumbnail, probe.
- `render/pipeline.py`: `validate`, `render_stills`, `render_video`, `prune_cache`. Scene clips are cached in `output/.cache/<key>.mp4`. The key covers the scene content, assets, encoding, and `CODE_FINGERPRINT` (a hash of the `render/*.py` sources). That means editing the renderer invalidates every cached clip automatically. Scenes are rendered in parallel with a ThreadPoolExecutor.
- `cli.py`: the Typer app (`explainer = explainer.cli:app`). `patterns/*.yaml` are narrative scaffolds, with metadata in their leading `# key: value` comments.
- `web/`: FastAPI + Jinja2 read-only browser (`create_app`).

## Conventions
- After changing models in `schema.py`, run `explainer schema` and commit the regenerated `schema/video.schema.json`. Also update `references/schema.md` if user-facing fields changed.
- New package data (templates, patterns, web files) must match the `package-data` globs in [pyproject.toml](pyproject.toml).
- `projects/*/output/` is generated and gitignored. Never edit it.
- Don't change built-in templates to fix one video. Use per-video/per-scene overrides or a project template with `extends:`.
- Asset paths in YAML are project-relative (`assets/x.jpg`). Never invent or download assets.
- Tests use the `ws` fixture in [tests/conftest.py](tests/conftest.py): a temporary `acme` project with generated images. Mark ffmpeg-dependent tests with `needs_ffmpeg`.
- Sample projects: `projects/sboms-101` (every layout, SBOM topic, bold-vertical) and `projects/git-tips` (custom `git-light` template that extends clean-light).
