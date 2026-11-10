---
name: explainer-video
description: Turn a brief into a finished short explainer video (square/vertical slideshow MP4) using the `explainer` CLI in this repo — picks a pattern and template, writes the scene YAML under projects/<project>/videos/, validates it, visually QAs stills, and renders. Use when the user gives a brief/topic and asks for a video, reel, short, slideshow, or explainer, or asks to write/edit a video script YAML.
---

# Explainer video from a brief

You turn a brief into `projects/<project>/videos/<slug>.yaml`, then validate, check visually, and render it with the `explainer` CLI. Work autonomously. Ask the user **only** if you can't tell which project the video belongs to and there's more than one plausible candidate.

The CLI is `.venv/bin/explainer`, or plain `explainer` if it's on PATH. Run it from the repo root (it reads `./projects`).

## Files to read first
- `references/schema.md` — every field and layout, with limits. **Read it before writing YAML.**
- `references/patterns.md` — narrative patterns, copywriting rules, and how to pick a template.
- The pattern scaffolds in `src/explainer/patterns/*.yaml` are complete, valid examples. `explainer new <project> <slug> --pattern <name>` copies one into place.

## Workflow

1. **Parse the brief** into: topic, audience, the single key message, 2–5 supporting points, CTA, target project, format, and target length. Defaults: format from `project.yaml` (else `vertical`), 20–30s, the CTA implied by the brief (or "Follow for more" via the template outro).

2. **Inspect the project**
   ```bash
   explainer list                      # projects + existing videos
   explainer templates -p <project>    # look & feel options
   explainer assets <project>          # images you may use (path, size, orientation)
   ```
   - Make a new project only if the brief asks for one: `explainer init <name> --template T --format F --handle @x`.
   - Look at the `explainer assets` output. To see what an image actually shows, Read it. **Use only asset paths that exist. Never invent or download images.** If nothing fits, use text-only layouts (`title`, `text`, `bullets`, `stat`, `quote`); they look intentional on their own.
   - Read one existing video in the project, if there is one, and keep its tone and style consistent.

3. **Pick a pattern** using the table in `references/patterns.md`. Scaffold it with `explainer new <project> <slug> --pattern <pattern> --title "..."`, then rewrite every placeholder. Or write the file directly in the same shape. The slug is kebab-case and describes the topic (`why-sboms-matter`).

4. **Write the copy** following the rules in `patterns.md`. The essentials:
   - The first scene is the hook: at most 8 words, a question or bold claim. Viewers decide in the first 2s.
   - One idea per scene. Bullets: at most 3–4 items, at most 6 words each. Body text: at most 20 words.
   - Wrap 1–3 key words per scene in `*asterisks*` to colour them with the accent colour. Never highlight a whole sentence.
   - Duration per scene ≈ `max(2.5, 1 + words / 4)` seconds. Staggered bullets need about 0.5s more per bullet.
   - The template's outro already adds a "Follow" scene with the handle. Don't add your own unless the CTA is different; in that case set `outro: false` or make your last scene the CTA.
   - Keep the total to about 15–45s unless the brief says otherwise.
   - Avoid emoji (the fonts can't render them; validation warns about this).

5. **Validate** and fix until there are no errors and no warnings you can avoid:
   ```bash
   explainer validate <project> <slug>
   ```
   Warnings to act on: "too fast to read" (lengthen the scene or cut words), "auto-shrunk" or "overflows" (cut words, don't just shrink), and bullet length.

6. **Visual QA.** Render stills and look at them:
   ```bash
   explainer still <project> <slug>      # writes output/stills/<slug>/NN-<id>.png + sheet.png
   ```
   Read `sheet.png` (all scenes in one image), then any individual frame that looks wrong. Check that:
   - text is legible against its background (contrast is auto-adjusted, but busy photos may need `background.overlay: 0.5–0.7`)
   - nothing is cropped and the text isn't cramped
   - the hook reads instantly
   - accent words are the right ones
   - images are cropped well (adjust `focus: [x, y]` or `fit: contain`)
   - the pacing feels varied, with no three identical layouts in a row

   Fix the YAML and repeat, for at most 3 passes.

7. **Render**
   ```bash
   explainer render <project> <slug>             # final 1080p -> projects/<project>/output/<slug>.mp4
   explainer render <project> <slug> --preview   # optional quick 540p check first
   ```
   Re-renders only rebuild the scenes that changed.

8. **Report** to the user: the YAML path, the MP4 path, the duration, the pattern and template you chose (one line on why), any remaining warnings, and that they can browse it with `explainer serve` (http://127.0.0.1:8000/p/<project>/v/<slug>).

## Editing an existing video
When asked to change a video, edit its YAML in place, keeping scene `id`s stable. Then validate, take stills, and render again. Only changed scenes re-render.

## Don'ts
- Don't edit files in `output/`. They're generated.
- Don't change built-in templates to fix one video. Use per-video `theme:`, `layouts:`, or per-scene `style:` overrides, or add a project template in `projects/<p>/templates/<name>.yaml` with `extends:`.
- Don't put the whole brief on screen. Social video copy is about a third the length of written copy.
