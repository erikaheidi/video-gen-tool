# Video script reference

The source of truth is `src/explainer/schema.py`. `explainer schema` writes the JSON Schema to `schema/video.schema.json`. Unknown keys are errors, which catches typos.

## Files
```
projects/<project>/
  project.yaml          # name, description, template, format, branding, theme overrides, tags
  assets/               # images/logos/fonts used by this project's videos (paths are project-relative)
  templates/<name>.yaml # optional project templates (can `extends:` a built-in)
  videos/<slug>.yaml    # one video script per file
  output/               # generated: <slug>.mp4, <slug>.jpg, <slug>.render.json, stills/, .cache/
```

## Merge order (later wins)
Template (with its `extends:` chain) → `project.yaml` (`theme`, `branding`, `format`) → video (`theme`, `defaults`, `layouts`, `branding`) → scene (`style`, `duration`, `transition`, ...). Dicts deep-merge; lists replace.

## Video file
```yaml
version: 1                 # always 1
title: Why SBOMs matter    # required; also fills {title}
description: optional note
template: bold-vertical    # optional, default from project.yaml
format: vertical           # square 1080x1080 | vertical 1080x1920 | portrait 1080x1350
fps: 30
intro: true                # include the template's intro scenes (if any)
outro: true                # include the template's outro scenes (usually a "Follow {handle}" card)
theme: { colors: { accent: "#22d3ee" } }             # partial override
defaults: { duration: 4, transition: { type: fade, duration: 0.45 }, text_animation: fade-in }
layouts: { bullets: { bullet: "✓" } }                # per-layout style override for this video
branding: { handle: "@me", logo: assets/logo.png, position: bottom-center, show_on_scenes: true }
scenes: [ ... ]            # required, at least 1
```

## Scene fields
| field | type | notes |
|---|---|---|
| `id` | kebab-case | stable id (default `scene-N`); used in warnings, stills, UI |
| `layout` | see below | required |
| `duration` | seconds | default from `defaults.duration` |
| `title`, `subtitle`, `body`, `caption` | text | `*word*` = accent colour; `\n` = line break |
| `bullets` | list of text | ≤ 4 items, ≤ 6 words each |
| `quote`, `author` | text | for `quote` |
| `value` | text | the big number for `stat`, e.g. `"96%"`, `"3x"`, `"$2M"` |
| `image` | path or `{src, fit: cover\|contain, motion, focus: [x, y]}` | project-relative, e.g. `assets/x.jpg` |
| `background` | `#hex`, path, or `{color, image, overlay: 0..1, overlay_color, motion, focus}` | `overlay` darkens images for legibility |
| `transition` | name or `{type, duration}` | transition **into** this scene |
| `text_animation` | `none` \| `fade-in` \| `stagger` | `stagger` reveals title then each bullet |
| `style` | LayoutStyle | per-scene visual overrides (below) |

**Transitions:** `none` (hard cut), `fade`, `fadeblack`, `dissolve`, `slideleft`, `slideright`, `slideup`, `slidedown`, `smoothleft`, `smoothup`, `wipeleft`, `wiperight`, `circleopen`. Transition durations are clamped to half of each neighbouring scene.

**Motion** (on background images, or `image` in the `image` layout): `none`, `zoom-in`, `zoom-out`, `pan-left`, `pan-right`.

**Placeholders** in any text: `{handle}`, `{title}`, `{project}`.

## Layouts
| layout | required | optional | use for |
|---|---|---|---|
| `title` | `title` | `subtitle`, `background` | hooks, section headers, CTA |
| `text` | `body` | `title` (small accent kicker above body) | definitions, one-sentence points, MYTH/FACT |
| `bullets` | `bullets` | `title` | 2–4 short points; pair with `text_animation: stagger` |
| `image` | `image` | `title`, `caption` | full-bleed photo with Ken Burns motion (default `zoom-in`) and text over a bottom gradient |
| `image-caption` | `image` + (`title` or `caption`) | | framed picture (screenshot, diagram, product) with text below |
| `split` | `image` + (`title` or `body`) | | image on top half (vertical) / left half (square) and text in the other half |
| `quote` | `quote` | `author` | testimonials, principles |
| `stat` | `value` | `title` (small label above), `caption` | one striking number |
| `outro` | `title` | `subtitle` | closing card; shows the project logo large |

## LayoutStyle (`style:` on a scene, `layouts:` in templates/videos)
`align: left|center|right`, `valign: top|middle|bottom`, `bullet: "•"`, `stagger: 0.45`, `title_scale: 1.0`, `body_scale: 1.0`, `uppercase_title: false`, `panel: false` (translucent box behind text, which helps on busy images), `panel_opacity: 0.75`, `accent_bar: false`, `text_color: "#hex"`, `title_color: "#hex"`.

## Theme (templates, `project.yaml`, video `theme:`)
`colors: {background, text, accent, muted, panel}`, `fonts: {heading, body}` (a font file name like `NotoSans-ExtraBold`, or a project path like `assets/fonts/Brand.ttf`), `sizes: {title, subtitle, body, caption, stat, handle}` (px at 1080 width), `padding`, `line_spacing`, `safe_area: {vertical: {top, bottom, left, right}, ...}`.

## Automatic behaviour
- Text auto-fits: the layout shrinks until it fits. Validation warns below 80% size and when text overflows.
- When text colour has less than 3:1 contrast with what's behind it (photos, panels), it switches to light or dark text automatically; accents are lightened or darkened.
- Vertical format keeps text inside a safe area (top 200px, bottom 340px) clear of platform UI.
