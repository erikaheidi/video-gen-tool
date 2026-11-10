"""Render a ResolvedScene into image layers with Pillow.

Each scene becomes:
  * ``bg.png``      – background colour/image (oversized when it has motion)
  * ``chrome.png``  – static overlay that never animates (gradients, split
                      images, branding watermark)
  * ``fg-N.png``    – text/element groups that fade in (one per stagger step)

ffmpeg (see ffmpeg.py) then animates and composites these layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from ..loader import ProjectRef
from ..schema import ResolvedScene, ResolvedVideo, effective_overlap
from .layout import (FontNotFound, TextBlock, draw_block, find_font, font_for, has_glyphs, hex_to_rgba, layout_text,
                     load_font, plain)

MOTION_HEADROOM = 1.2   # background oversize factor when the background moves
FADE_DURATION = 0.35
MIN_FIT = 0.5           # never shrink a layout below 50% of its design size


# --------------------------------------------------------------------------- data


@dataclass
class Layer:
    path: Path
    start: float = 0.0   # fade-in start (s, scene-local)
    fade: float = 0.0    # fade-in duration (0 = visible from the first frame)


@dataclass
class SceneLayers:
    scene_id: str
    duration: float
    bg: Path
    motion: str
    chrome: Path | None
    fg: list[Layer]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


DrawFn = Callable[[Image.Image, float, float, float], None]  # (img, x, y, box_width)


@dataclass
class Item:
    """A vertically stacked element: knows its height and how to draw itself."""

    height: float
    draw: DrawFn
    group: int = 0
    gap_after: float = 0.0
    overflow: bool = False
    missing_glyphs: str = ""


# --------------------------------------------------------------------------- renderer


class SceneRenderer:
    """Renders scenes of one video. Not thread-safe: use one instance per thread."""

    def __init__(self, video: ResolvedVideo, project: ProjectRef):
        self.v = video
        self.project = project
        self.W, self.H = video.width, video.height
        self.s = video.scale
        t = video.theme
        self.colors = t.colors
        self.sizes = t.sizes
        self.line_spacing = t.line_spacing
        pad = t.padding * self.s
        safe = t.safe_area.get(video.format)
        sl, st, sr, sb = ((safe.left, safe.top, safe.right, safe.bottom) if safe else (0, 0, 0, 0))
        self.pad = pad
        self.safe_bottom = sb * self.s
        self.box = Box(pad + sl * self.s, pad + st * self.s, self.W - pad - sr * self.s, self.H - pad - sb * self.s)
        b = video.branding
        if b.show_on_scenes and (b.handle or b.logo):
            # keep the watermark's strip free of content
            reserve = t.sizes.handle * 1.5 * self.s + pad * 0.4
            if b.position.startswith("bottom"):
                self.box.y1 -= reserve
            else:
                self.box.y0 += reserve
        self.heading_font = self._font(t.fonts.heading)
        self.body_font = self._font(t.fonts.body)
        self._lum: float | None = None       # luminance behind the current scene's text
        self._small_bgs: dict[str, Image.Image] = {}

    def _font(self, name: str) -> str:
        try:
            return find_font(name, str(self.project.path))
        except FontNotFound:
            return find_font("NotoSans-Bold") if "bold" in name.lower() else find_font("NotoSans-Regular")

    # ------------------------------------------------------------------ public
    def render(self, scene: ResolvedScene, out_dir: Path) -> SceneLayers:
        out_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        bg = self.background(scene)
        bg_path = out_dir / "bg.png"
        bg.save(bg_path, compress_level=1)

        chrome = self.chrome(scene)
        chrome_path = None
        if chrome is not None:
            chrome_path = out_dir / "chrome.png"
            chrome.save(chrome_path, compress_level=1)

        groups, warn = self.foreground_groups(scene)
        warnings += warn
        fg_layers = []
        for gi, (start, fade, img) in enumerate(self._timed_groups(scene, groups)):
            p = out_dir / f"fg-{gi}.png"
            img.save(p, compress_level=1)
            fg_layers.append(Layer(p, start, fade))
        return SceneLayers(scene.id, scene.duration, bg_path, scene.background.motion, chrome_path, fg_layers, warnings)

    def still(self, scene: ResolvedScene) -> tuple[Image.Image, list[str]]:
        """Final-state frame of a scene (all text visible), for previews and QA."""
        bg = self.background(scene)
        if bg.size != (self.W, self.H):
            bg = ImageOps.fit(bg, (self.W, self.H))
        frame = bg.convert("RGBA")
        chrome = self.chrome(scene)
        if chrome is not None:
            frame.alpha_composite(chrome)
        groups, warnings = self.foreground_groups(scene)
        for img in groups:
            frame.alpha_composite(img)
        return frame.convert("RGB"), warnings

    def check(self, scene: ResolvedScene) -> list[str]:
        """Layout warnings without rendering backgrounds (used by `validate`)."""
        return self.foreground_groups(scene)[1]

    # ------------------------------------------------------------------ timing
    def _timed_groups(self, scene: ResolvedScene, groups: list[Image.Image]):
        anim = scene.text_animation
        if anim == "none" or not groups:
            if groups:
                merged = Image.new("RGBA", (self.W, self.H))
                for g in groups:
                    merged.alpha_composite(g)
                yield 0.0, 0.0, merged
            return
        overlap = effective_overlap(self.v.scenes, scene.index)
        base = 0.0 if scene.index == 0 else min(0.15 + overlap * 0.5, scene.duration * 0.3)
        latest = max(base, scene.duration * 0.6)
        if anim == "fade-in":
            merged = Image.new("RGBA", (self.W, self.H))
            for g in groups:
                merged.alpha_composite(g)
            yield base, FADE_DURATION, merged
            return
        for i, g in enumerate(groups):  # stagger
            yield min(base + i * scene.style.stagger, latest), FADE_DURATION, g

    # ------------------------------------------------------------------ background
    def background(self, scene: ResolvedScene, size: tuple[int, int] | None = None) -> Image.Image:
        bgs = scene.background
        if size is None:
            k = MOTION_HEADROOM if bgs.motion != "none" else 1.0
            size = (_even(self.W * k), _even(self.H * k))
        base = Image.new("RGB", size, bgs.color or self.colors.background)
        if bgs.image:
            src = self._open(bgs.image).convert("RGB")
            base = ImageOps.fit(src, size, Image.LANCZOS, centering=bgs.focus)
        if bgs.overlay > 0:
            tint = Image.new("RGB", size, bgs.overlay_color or "#000000")
            base = Image.blend(base, tint, bgs.overlay)
        return base

    # ------------------------------------------------------------------ contrast
    def _small_bg(self, scene: ResolvedScene) -> Image.Image:
        """Low-res canvas-aligned background (with split image) for luminance sampling."""
        if scene.id not in self._small_bgs:
            f = 8
            img = self.background(scene, (max(1, self.W // f), max(1, self.H // f)))
            if scene.layout == "split" and scene.image:
                r = self._split_regions()[0]
                pic = ImageOps.fit(self._open(scene.image.src).convert("RGB"),
                                   (max(1, int(r.w / f)), max(1, int(r.h / f))), centering=scene.image.focus)
                img.paste(pic, (int(r.x0 / f), int(r.y0 / f)))
            self._small_bgs[scene.id] = img
        return self._small_bgs[scene.id]

    def _region_lum(self, scene: ResolvedScene, box: Box) -> float:
        img = self._small_bg(scene)
        f = self.W / img.width
        crop = img.crop((int(box.x0 / f), int(box.y0 / f), max(int(box.x0 / f) + 1, int(box.x1 / f)),
                         max(int(box.y0 / f) + 1, int(box.y1 / f))))
        r, g, b = ImageStat.Stat(crop).mean[:3]
        return _luminance((r, g, b))

    def _backdrop(self, scene: ResolvedScene, box: Box) -> float | None:
        if scene.style.panel:
            return _luminance(hex_to_rgba(self.colors.panel)[:3])
        if scene.layout == "image":
            return None  # light text over a dark gradient, colours chosen explicitly
        return self._region_lum(scene, box)

    def _adapt(self, color: str, kind: str = "text", lum: float | None = ...) -> str:
        """Swap ``color`` for black/white variants when it lacks contrast with the backdrop."""
        lum = self._lum if lum is ... else lum
        if lum is None:
            return color
        rgb = hex_to_rgba(color)[:3]
        if _contrast(_luminance(rgb), lum) >= 3.0:
            return color
        if kind == "accent":
            # keep the hue: blend towards white (dark backdrop) or black (light backdrop)
            target = (255, 255, 255) if lum < 0.18 else (0, 0, 0)
            for i in range(1, 8):
                t = i / 8
                mixed = tuple(int(c + (tc - c) * t) for c, tc in zip(rgb, target))
                if _contrast(_luminance(mixed), lum) >= 3.0:
                    return "#%02x%02x%02x" % mixed
            kind = "text"
        light, dark = ("#d1d5db", "#4b5563") if kind == "muted" else ("#ffffff", "#111827")
        lw, dk = _luminance(hex_to_rgba(light)[:3]), _luminance(hex_to_rgba(dark)[:3])
        return light if _contrast(lw, lum) >= _contrast(dk, lum) else dark

    def _open(self, rel: str) -> Image.Image:
        img = Image.open(self.project.resolve_path(rel))
        return ImageOps.exif_transpose(img)

    # ------------------------------------------------------------------ chrome
    def chrome(self, scene: ResolvedScene) -> Image.Image | None:
        img = Image.new("RGBA", (self.W, self.H))
        used = False
        if scene.layout == "image" and (scene.title or scene.caption) and not scene.style.panel:
            used = True
            img.alpha_composite(self._gradient())
        if scene.layout == "split" and scene.image:
            used = True
            region = self._split_regions()[0]
            pic = ImageOps.fit(self._open(scene.image.src).convert("RGB"), (int(region.w), int(region.h)),
                               Image.LANCZOS, centering=scene.image.focus)
            img.paste(pic, (int(region.x0), int(region.y0)))
        if self.v.branding.show_on_scenes and scene.layout != "outro":
            used |= self._watermark(img, scene)
        return img if used else None

    def _gradient(self) -> Image.Image:
        """Dark gradient over the lower part of the frame for text legibility."""
        g = Image.new("L", (1, 256))
        for y in range(256):
            g.putpixel((0, y), int(max(0.0, (y - 90) / 166) ** 1.4 * 235))
        alpha = g.resize((self.W, self.H))
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        layer.putalpha(alpha)
        return layer

    def _watermark(self, img: Image.Image, scene: ResolvedScene) -> bool:
        b = self.v.branding
        if not (b.handle or b.logo):
            return False
        size = int(self.sizes.handle * self.s)
        font_block = layout_text(b.handle, self.body_font, size, self.W) if b.handle else None
        logo = None
        if b.logo:
            logo = self._open(b.logo).convert("RGBA")
            lh = int(size * 1.5)
            logo = logo.resize((max(1, int(logo.width * lh / logo.height)), lh), Image.LANCZOS)
        gap = size * 0.4
        w = (logo.width if logo else 0) + (font_block.width if font_block else 0) + (gap if logo and font_block else 0)
        h = max(logo.height if logo else 0, font_block.height if font_block else 0)
        margin = self.pad * 0.55
        pos = b.position
        x = {"left": margin, "right": self.W - margin - w}.get(pos.split("-")[1], (self.W - w) / 2)
        if scene.layout == "split" and self.H <= self.W * 1.1 and pos.endswith("center"):
            tb = self._split_regions()[1]
            x = tb.x0 + (tb.w - w) / 2
        if pos.startswith("bottom"):
            y = self.H - self.safe_bottom - margin - h if self.safe_bottom else self.H - margin - h
        else:
            y = margin
        lum = self._region_lum(scene, Box(x, y, x + max(w, 1), y + max(h, 1)))
        layer = Image.new("RGBA", img.size)
        d = ImageDraw.Draw(layer)
        cx = x
        if logo:
            layer.alpha_composite(logo, (int(cx), int(y + (h - logo.height) / 2)))
            cx += logo.width + gap
        if font_block:
            text_col = self.colors.text if scene.layout != "image" else "#ffffff"
            draw_block(d, font_block, cx, y + (h - font_block.height) / 2, font_block.width, "left",
                       hex_to_rgba(self._adapt(text_col, lum=lum)), hex_to_rgba(self._adapt(self.colors.accent, "accent", lum)))
        if b.opacity < 1:
            a = layer.getchannel("A").point(lambda v: int(v * b.opacity))
            layer.putalpha(a)
        img.alpha_composite(layer)
        return True

    def _split_regions(self) -> tuple[Box, Box]:
        """(image region, text box) for the split layout."""
        if self.H > self.W * 1.1:  # tall formats: image on top
            img = Box(0, 0, self.W, self.H * 0.48)
            text = Box(self.box.x0, img.y1 + self.pad * 0.6, self.box.x1, self.box.y1)
        else:  # square: image left
            img = Box(0, 0, self.W * 0.48, self.H)
            text = Box(img.x1 + self.pad * 0.7, self.box.y0, self.box.x1, self.box.y1)
        return img, text

    # ------------------------------------------------------------------ foreground
    def foreground_groups(self, scene: ResolvedScene) -> tuple[list[Image.Image], list[str]]:
        """Lay out the scene's elements, auto-fitting to the content box."""
        box = self._split_regions()[1] if scene.layout == "split" else self.box
        builder = getattr(self, "_l_" + scene.layout.replace("-", "_"))
        self._lum = self._backdrop(scene, box)
        warnings: list[str] = []
        k = 1.0
        while True:
            items = builder(scene, box, k)
            total = sum(i.height + i.gap_after for i in items) - (items[-1].gap_after if items else 0)
            if total <= box.h or k <= MIN_FIT:
                break
            k = max(MIN_FIT, k * 0.92)
        if total > box.h:
            warnings.append(f"scene {scene.id}: text overflows the frame even at {int(k * 100)}% size — shorten it")
        elif k < 0.8:
            warnings.append(f"scene {scene.id}: text auto-shrunk to {int(k * 100)}% to fit — consider shortening")
        if any(i.overflow for i in items):
            warnings.append(f"scene {scene.id}: a word is too long to fit the line width")
        for i in items:
            if i.missing_glyphs:
                warnings.append(f"scene {scene.id}: font cannot render some characters (emoji?) in {i.missing_glyphs!r}")
        return self._compose(scene, items, box, total), warnings

    def _compose(self, scene: ResolvedScene, items: list[Item], box: Box, total: float) -> list[Image.Image]:
        valign = scene.style.valign
        y = {"top": box.y0, "middle": box.y0 + (box.h - total) / 2, "bottom": box.y1 - total}[valign]
        y = max(y, box.y0) if total <= box.h else box.y0 - (total - box.h) / 2
        n_groups = max((i.group for i in items), default=-1) + 1
        groups = [Image.new("RGBA", (self.W, self.H)) for _ in range(n_groups)]
        if scene.style.panel and items:
            m = self.pad * 0.45
            panel = Image.new("RGBA", (self.W, self.H))
            ImageDraw.Draw(panel).rounded_rectangle(
                (box.x0 - m, y - m, box.x1 + m, y + total + m), radius=int(28 * self.s),
                fill=hex_to_rgba(self.colors.panel, scene.style.panel_opacity))
            groups[0] = Image.alpha_composite(panel, groups[0])
        for it in items:
            it.draw(groups[it.group], box.x0, y, box.w)
            y += it.height + it.gap_after
        return [g for g in groups if g.getbbox()]

    # ------------------------------------------------------------------ item helpers
    def _text(self, text: str, font: str, size: float, box: Box, *, color: str, group: int = 0, gap: float = 0,
              align: str = "left", upper: bool = False, max_width: float | None = None) -> Item:
        text = text.upper() if upper else text
        # balance headlines and centred text; ragged-right body copy reads better unbalanced
        balance = align == "center" or font == self.heading_font
        block = layout_text(text, font, int(size * self.s), max_width or box.w, line_spacing=self.line_spacing,
                            balance=balance)
        kind = "accent" if color == self.colors.accent else "muted" if color == self.colors.muted else "text"
        accent = hex_to_rgba(self._adapt(self.colors.accent, "accent"))
        col = hex_to_rgba(self._adapt(color, kind))

        def draw(img: Image.Image, x: float, y: float, w: float, block: TextBlock = block) -> None:
            draw_block(ImageDraw.Draw(img), block, x, y, w, align, col, accent)

        missing = "" if has_glyphs(font, plain(text)) else plain(text)
        return Item(block.height, draw, group, gap * self.s, block.overflow, missing)

    def _bar(self, align: str, group: int = 0) -> Item:
        bw, bh = 120 * self.s, 12 * self.s
        accent = hex_to_rgba(self._adapt(self.colors.accent, "accent"))

        def draw(img: Image.Image, x: float, y: float, w: float) -> None:
            bx = {"center": x + (w - bw) / 2, "right": x + w - bw}.get(align, x)
            ImageDraw.Draw(img).rounded_rectangle((bx, y, bx + bw, y + bh), radius=int(bh / 2), fill=accent)

        return Item(bh, draw, group, 36 * self.s)

    def _picture(self, rel: str, max_w: float, max_h: float, align: str, group: int = 0, gap: float = 0,
                 fit: str = "contain", radius: float = 28) -> Item:
        src = self._open(rel).convert("RGBA")
        if fit == "cover":
            pic = ImageOps.fit(src, (int(max_w), int(max_h)), Image.LANCZOS)
        else:
            pic = ImageOps.contain(src, (int(max_w), int(max_h)), Image.LANCZOS)
        if radius:
            mask = Image.new("L", pic.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, *pic.size), radius=int(radius * self.s), fill=255)
            pic.putalpha(Image.composite(pic.getchannel("A"), mask, mask))
            shadow = Image.new("RGBA", (pic.width + 80, pic.height + 80))
            ImageDraw.Draw(shadow).rounded_rectangle((40, 50, 40 + pic.width, 50 + pic.height),
                                                     radius=int(radius * self.s), fill=(0, 0, 0, 110))
            shadow = shadow.filter(ImageFilter.GaussianBlur(18 * self.s))
        else:
            shadow = None

        def draw(img: Image.Image, x: float, y: float, w: float) -> None:
            px = {"center": x + (w - pic.width) / 2, "right": x + w - pic.width}.get(align, x)
            if shadow is not None:
                img.alpha_composite(shadow, (int(px - 40), int(y - 40)))
            img.alpha_composite(pic, (int(px), int(y)))

        return Item(pic.height, draw, group, gap * self.s)

    def _title_color(self, scene: ResolvedScene) -> str:
        return scene.style.title_color or scene.style.text_color or self.colors.text

    def _text_color(self, scene: ResolvedScene) -> str:
        return scene.style.text_color or self.colors.text

    # ------------------------------------------------------------------ layouts
    # Each returns stacked Items for scale factor k (k shrinks during auto-fit).

    def _l_title(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        items = [self._bar(a)] if st.accent_bar else []
        items.append(self._text(sc.title, self.heading_font, self.sizes.title * st.title_scale * k, box,
                                color=self._title_color(sc), align=a, upper=st.uppercase_title, gap=34))
        if sc.subtitle:
            items.append(self._text(sc.subtitle, self.body_font, self.sizes.subtitle * st.body_scale * k, box,
                                    color=self.colors.muted, align=a, group=1))
        return items

    def _l_text(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        items: list[Item] = []
        if st.accent_bar:
            items.append(self._bar(a))
        if sc.title:
            items.append(self._text(sc.title, self.heading_font, self.sizes.title * 0.62 * st.title_scale * k, box,
                                    color=st.title_color or self.colors.accent, align=a, upper=st.uppercase_title, gap=30))
        items.append(self._text(sc.body, self.body_font, self.sizes.body * 1.12 * st.body_scale * k, box,
                                color=self._text_color(sc), align=a, group=1 if sc.title else 0))
        return items

    def _l_bullets(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        items: list[Item] = []
        g = 0
        if sc.title:
            items.append(self._text(sc.title, self.heading_font, self.sizes.title * 0.78 * st.title_scale * k, box,
                                    color=self._title_color(sc), align=a, upper=st.uppercase_title, gap=56))
            g = 1
        size = self.sizes.body * st.body_scale * k
        for i, b in enumerate(sc.bullets or []):
            items.append(self._bullet(b, size, box, st.bullet, g + i, gap=40 * k, align=a, color=self._text_color(sc)))
        return items

    def _bullet(self, text: str, size: float, box: Box, glyph: str, group: int, gap: float, align: str, color: str) -> Item:
        px = int(size * self.s)
        block = layout_text(text, self.body_font, px, box.w * 0.88, line_spacing=self.line_spacing)
        glyph_font = font_for(glyph, self.heading_font, self.body_font) if glyph else None
        gfont = gbox = None
        if glyph_font:
            # symbol fonts draw small: scale the glyph so its height is ~ the text's x-height * 1.3
            text_font = block.font
            xh = -text_font.getbbox("x", anchor="ls")[1]
            probe = load_font(glyph_font, block.size).getbbox(glyph, anchor="ls")
            gh = max(1, probe[3] - probe[1])
            target = max(xh * 1.3, gh) if gh < xh else gh
            gfont = load_font(glyph_font, int(block.size * target / gh))
            gbox = gfont.getbbox(glyph, anchor="ls")
        indent = ((gbox[2] - gbox[0]) + px * 0.5) if gbox else 0
        if indent:
            block = layout_text(text, self.body_font, px, box.w - indent, line_spacing=self.line_spacing)
        accent, col = hex_to_rgba(self._adapt(self.colors.accent, "accent")), hex_to_rgba(self._adapt(color))

        def draw(img: Image.Image, x: float, y: float, w: float) -> None:
            d = ImageDraw.Draw(img)
            total_w = indent + block.width
            ox = {"center": x + (w - total_w) / 2, "right": x + w - total_w}.get(align, x)
            if gfont is not None:
                asc, _ = block.font.getmetrics()
                xh = -block.font.getbbox("x", anchor="ls")[1]
                centre = y + asc - xh / 2                       # middle of the first line's x-height
                gy = centre - (gbox[1] + gbox[3]) / 2           # baseline that centres the glyph there
                d.text((ox - gbox[0], gy), glyph, font=gfont, fill=accent, anchor="ls")
            draw_block(d, block, ox + indent, y, block.width if align != "left" else w - indent, "left", col, accent)

        return Item(block.height, draw, group, gap * self.s, block.overflow)

    def _l_image(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        items: list[Item] = []
        if sc.title:
            items.append(self._text(sc.title, self.heading_font, self.sizes.title * 0.8 * st.title_scale * k, box,
                                    color=st.title_color or "#ffffff" if not st.panel else self._title_color(sc),
                                    align=a, upper=st.uppercase_title, gap=24))
        if sc.caption:
            items.append(self._text(sc.caption, self.body_font, self.sizes.caption * st.body_scale * k, box,
                                    color=(st.text_color or "#e5e7eb") if not st.panel else self._text_color(sc),
                                    align=a, group=1 if sc.title else 0))
        return items

    def _l_image_caption(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        frac = 0.58 if self.H > self.W * 1.1 else 0.55
        items = [self._picture(sc.image.src, box.w, box.h * frac * min(1.0, k / 0.9), a, gap=56,
                               fit=sc.image.fit)]
        if sc.title:
            items.append(self._text(sc.title, self.heading_font, self.sizes.title * 0.68 * st.title_scale * k, box,
                                    color=self._title_color(sc), align=a, upper=st.uppercase_title, group=1, gap=22))
        if sc.caption:
            items.append(self._text(sc.caption, self.body_font, self.sizes.caption * st.body_scale * k, box,
                                    color=self.colors.muted, align=a, group=2 if sc.title else 1))
        return items

    def _l_split(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        square = self.H <= self.W * 1.1
        items: list[Item] = []
        if sc.title:
            items.append(self._text(sc.title, self.heading_font,
                                    self.sizes.title * (0.55 if square else 0.72) * st.title_scale * k, box,
                                    color=self._title_color(sc), align=a, upper=st.uppercase_title, gap=28))
        if sc.body:
            items.append(self._text(sc.body, self.body_font, self.sizes.body * (0.8 if square else 1.0) * st.body_scale * k,
                                    box, color=self._text_color(sc), align=a, group=1 if sc.title else 0))
        return items

    def _l_quote(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        mark = self._text("“", self.heading_font, self.sizes.title * 2.2 * k, box, color=self.colors.accent, align=a)
        mark.height *= 0.55  # the glyph's box is mostly empty space
        items = [mark]
        items.append(self._text(sc.quote, self.heading_font, self.sizes.body * 1.25 * st.body_scale * k, box,
                                color=self._text_color(sc), align=a, gap=44))
        if sc.author:
            items.append(self._text(f"— {plain(sc.author)}", self.body_font, self.sizes.caption * k, box,
                                    color=self.colors.muted, align=a, group=1))
        return items

    def _l_stat(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        items: list[Item] = []
        if sc.title:
            items.append(self._text(sc.title, self.body_font, self.sizes.subtitle * k, box, color=self.colors.muted,
                                    align=a, upper=st.uppercase_title, gap=10))
        items.append(self._text(sc.value, self.heading_font, self.sizes.stat * st.title_scale * k, box,
                                color=st.title_color or self.colors.accent, align=a, gap=20))
        if sc.caption:
            items.append(self._text(sc.caption, self.body_font, self.sizes.body * st.body_scale * k, box,
                                    color=self._text_color(sc), align=a, group=1))
        return items

    def _l_outro(self, sc: ResolvedScene, box: Box, k: float) -> list[Item]:
        st, a = sc.style, sc.style.align
        items: list[Item] = []
        if self.v.branding.logo:
            items.append(self._picture(self.v.branding.logo, box.w * 0.4 * k, box.h * 0.22 * k, a, gap=56,
                                       radius=0))
        items.append(self._text(sc.title, self.heading_font, self.sizes.title * st.title_scale * k, box,
                                color=self._title_color(sc), align=a, upper=st.uppercase_title, gap=30))
        if sc.subtitle:
            items.append(self._text(sc.subtitle, self.body_font, self.sizes.subtitle * 1.1 * st.body_scale * k, box,
                                    color=self.colors.accent, align=a, group=1))
        return items


def _luminance(rgb: tuple) -> float:
    """WCAG relative luminance of an sRGB colour (0..255 channels)."""
    def lin(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _even(x: float) -> int:
    return int(round(x / 2) * 2)
