# Patterns, templates and copywriting rules

## Choose a pattern
| brief looks like | pattern | scenes |
|---|---|---|
| "explain X", "why X matters", general topic | `hook-points-cta` (default) | hook → context → 3 points → payoff → CTA |
| selling a tool or practice, "stop doing X" | `problem-solution` | hook → pain → cost (stat) → solution → how → CTA |
| "N tips / N mistakes / N things" | `listicle` | hook → one scene per item → recap → CTA |
| tutorial, "how to", setup steps | `how-to` | hook → what you need → step per scene → result |
| misconceptions, "actually..." | `myth-vs-fact` | hook → (MYTH, FACT) × 2–3 → takeaway |
| the brief contains a striking number | `stat-spotlight` | stat → meaning → why → action |
| a single quote or principle | `quote-card` | hook → quote → takeaway (≈10s) |

You can mix patterns. For example, add a `stat` scene to a listicle or open a how-to with a `problem-solution` hook. Keep the overall arc: **hook → value → payoff/CTA**.

## Choose a template
Use the project's template unless the brief asks for a different look. Built-ins:
- `bold-vertical`: dark navy with yellow accent and heavy sans headlines. Energetic; the default for Reels, Shorts, and TikTok.
- `clean-light`: off-white with a blue accent and serif headlines. Editorial, calm; good for LinkedIn and education.
- `dark-square`: bold-vertical tuned for 1:1 feeds, with a cyan accent and slide transitions.

Formats: `vertical` for Reels, Shorts, TikTok, and Stories; `square` for feed posts, LinkedIn, and X; `portrait` (4:5) for Instagram and Facebook feeds.

## Using assets
- Put photos with clear subjects in `image` (full-bleed, moving) or as a `title` background with `overlay: 0.5–0.65`.
- Put screenshots, diagrams, and product shots in `image-caption` (framed, `fit: contain` for screenshots).
- Landscape images in vertical videos work best in `image-caption` or `split`. Full-bleed `image` crops them heavily; set `focus: [x, y]` to choose the crop centre.
- Reuse the same image at most twice in one video.
- Transparent PNG logos come from `branding.logo` automatically. Don't place them as scene images.

## Copywriting rules (on-screen text)
1. **Hook in at most 8 words.** A question ("Do you know what's inside your containers?"), a bold claim ("Your dependencies are lying to you"), or a number ("96% of apps ship open source").
2. **One idea per scene.** If you need "and", split the scene.
3. **Short lines:**
   - titles at most 8 words
   - body at most 20 words
   - bullets at most 6 words each and at most 4 per scene
   - captions at most 10 words
4. **Concrete over abstract.** Prefer "Find CVEs in seconds" over "Improve your security posture".
5. **Accent 1–3 words per scene** with `*asterisks*`, on the word that carries the meaning.
6. **Parallel bullets:** start each with the same part of speech ("Every package / Every version / Every license").
7. **Sentence case** everywhere (`uppercase_title` is for labels like MYTH or FACT).
8. **CTA is an action:** "Generate your first SBOM today", "Save this for later", "Follow for part 2".
9. **No emoji, hashtags, or URLs longer than a domain.** The fonts don't render emoji, and long URLs are unreadable on a phone.

## Pacing
- Scene duration ≈ `max(2.5, 1 + words / 4)` seconds. Hooks run 2.5–3.5s.
- Staggered bullets: add about 0.5s per bullet beyond the first.
- Vary layouts. Don't put three identical layouts in a row; break runs with a `stat`, `quote`, or image scene.
- Transitions: keep the template default for most scenes. Use `none` (hard cut) or `slideleft` for punchy reveals (MYTH→FACT), and `fadeblack` before an ending.
- Use `motion: zoom-in` for hooks and `pan-left` or `pan-right` for wide images. Don't put motion on every scene.

## Worked example (brief → YAML)
Brief: *"30s vertical for the sboms-101 project: why teams should generate SBOMs. Audience: developers. CTA: add it to CI."*

```yaml
version: 1
title: Why generate SBOMs
scenes:
  - id: hook
    layout: title
    duration: 3
    title: Do you know what's *inside* your containers?
    background: { image: assets/containers.jpg, overlay: 0.6, motion: zoom-in }
  - id: definition
    layout: text
    duration: 4.5
    title: SBOM
    body: A *Software Bill of Materials* lists every component in your software.
  - id: stat
    layout: stat
    duration: 3.5
    title: Open source is everywhere
    value: "96%"
    caption: of codebases contain open source
  - id: benefits
    layout: bullets
    duration: 5
    title: With an SBOM you can
    text_animation: stagger
    bullets: [Find *CVEs* in seconds, Prove license compliance, Answer audits fast]
  - id: how
    layout: image-caption
    duration: 4
    image: { src: assets/laptop.png, fit: contain }
    title: Generate it at *build time*
    caption: One CI step, SPDX or CycloneDX
  - id: cta
    layout: title
    duration: 3
    title: Add an SBOM step to *CI* today
```
