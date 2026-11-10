"""Text layout primitives on top of Pillow: font lookup, rich text, wrapping, auto-fit.

Rich text: wrap words in ``*asterisks*`` to colour them with the accent colour,
e.g. ``"Ship *faster* with SBOMs"``. Explicit newlines force line breaks.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageDraw, ImageFont

FONT_DIRS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
             Path("/Library/Fonts"), Path("C:/Windows/Fonts")]
FONT_EXTS = (".ttf", ".otf", ".ttc")


class FontNotFound(Exception):
    pass


@functools.lru_cache(maxsize=256)
def find_font(name: str, project_dir: str | None = None) -> str:
    """Resolve a font name or path to a font file.

    Order: project-relative path, absolute path, ``<name>.ttf/otf`` under the
    system font dirs, then ``fc-match``.
    """
    candidates: list[Path] = []
    if project_dir:
        candidates.append(Path(project_dir) / name)
    candidates.append(Path(name))
    for c in candidates:
        if c.is_file():
            return str(c)
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for ext in FONT_EXTS:
            hits = sorted(d.rglob(f"{name}{ext}"))
            if hits:
                return str(hits[0])
    if shutil.which("fc-match"):
        res = subprocess.run(["fc-match", "-f", "%{file}", name], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout and Path(res.stdout).is_file():
            return res.stdout
    raise FontNotFound(f"font {name!r} not found (install it or use a path inside the project)")


@functools.lru_cache(maxsize=512)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(1, int(size)))


# --------------------------------------------------------------------------- rich text


@dataclass(frozen=True)
class Word:
    text: str
    accent: bool = False


def parse_rich(text: str) -> list[list[list[Word]]]:
    """Split text into paragraphs -> words -> styled pieces, tracking *accent* spans.

    ``"Ship *fast*er"`` gives one paragraph with words ``[Ship]`` and ``[fast*, er]``.
    """
    paragraphs: list[list[list[Word]]] = []
    accent = False
    for para in text.split("\n"):
        words: list[list[Word]] = []
        for raw in para.split():
            pieces: list[Word] = []
            for tok in re.split(r"(\*)", raw):
                if tok == "*":
                    accent = not accent
                elif tok:
                    pieces.append(Word(tok, accent))
            if pieces:
                words.append(pieces)
        paragraphs.append(words)
    return paragraphs


def plain(text: str) -> str:
    return text.replace("*", "")


# --------------------------------------------------------------------------- wrapping


@dataclass
class TextBlock:
    lines: list[list[list[Word]]]  # lines -> words -> styled pieces
    font: ImageFont.FreeTypeFont
    size: int
    line_height: int
    width: int
    height: int
    overflow: bool = False

    @property
    def space(self) -> float:
        return self.font.getlength(" ")


def _word_width(font: ImageFont.FreeTypeFont, word: list[Word]) -> float:
    return sum(font.getlength(p.text) for p in word)


def _line_width(font: ImageFont.FreeTypeFont, line: list[list[Word]]) -> float:
    if not line:
        return 0.0
    return sum(_word_width(font, w) for w in line) + font.getlength(" ") * (len(line) - 1)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> tuple[list[list[list[Word]]], bool]:
    """Greedy word wrap. Returns lines and whether any single word is wider than max_width."""
    lines: list[list[list[Word]]] = []
    too_wide = False
    space = font.getlength(" ")
    for words in parse_rich(text):
        if not words:
            lines.append([])
            continue
        cur: list[list[Word]] = []
        cur_w = 0.0
        for w in words:
            ww = _word_width(font, w)
            if ww > max_width:
                too_wide = True
            if cur and cur_w + space + ww > max_width:
                lines.append(cur)
                cur, cur_w = [w], ww
            else:
                cur_w = cur_w + (space if cur else 0) + ww
                cur.append(w)
        lines.append(cur)
    return _balance(lines, font, max_width), too_wide


def _balance(lines: list[list[list[Word]]], font: ImageFont.FreeTypeFont, max_width: float) -> list[list[list[Word]]]:
    """Avoid a lonely last word ("widow") by pulling one word down when it fits."""
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
        moved = lines[-2][-1]
        candidate = [moved] + lines[-1]
        if _line_width(font, candidate) <= max_width:
            lines = lines[:-2] + [lines[-2][:-1], candidate]
    return lines


def balanced_wrap(text: str, font: ImageFont.FreeTypeFont, max_width: float, n_lines: int) -> list[list[list[Word]]]:
    """Narrowest wrap that keeps ``n_lines`` lines, so line lengths come out even."""
    lo, hi = max_width * 0.4, max_width
    best = wrap(text, font, max_width)[0]
    for _ in range(12):
        mid = (lo + hi) / 2
        lines, too_wide = wrap(text, font, mid)
        if not too_wide and len(lines) <= n_lines:
            best, hi = lines, mid
        else:
            lo = mid
    return best


def layout_text(text: str, font_path: str, size: int, max_width: float, max_height: float | None = None,
                line_spacing: float = 1.18, min_size: int | None = None, max_lines: int | None = None,
                balance: bool = True) -> TextBlock:
    """Wrap text at ``size``, shrinking until it fits the box (auto-fit).

    ``overflow`` is set when the text still does not fit at ``min_size``.
    """
    size = int(size)
    min_size = int(min_size or max(12, size * 0.55))
    while True:
        font = load_font(font_path, size)
        lines, too_wide = wrap(text, font, max_width)
        line_h = int(round(size * line_spacing))
        asc, desc = font.getmetrics()
        height = line_h * (len(lines) - 1) + asc + desc if lines else 0
        width = int(max((_line_width(font, ln) for ln in lines), default=0))
        fits = not too_wide and (max_height is None or height <= max_height) and (max_lines is None or len(lines) <= max_lines)
        if fits or size <= min_size:
            if fits and balance and len(lines) > 1:
                lines = balanced_wrap(text, font, max_width, len(lines))
                width = int(max((_line_width(font, ln) for ln in lines), default=0))
            return TextBlock(lines, font, size, line_h, width, height, overflow=not fits)
        size = max(min_size, int(size * 0.93))


def draw_block(draw: ImageDraw.ImageDraw, block: TextBlock, x: float, y: float, box_width: float, align: str,
               color: str | tuple, accent: str | tuple, uppercase: bool = False) -> None:
    """Draw a TextBlock with its top-left box corner at (x, y)."""
    space = block.space
    asc, _ = block.font.getmetrics()
    for i, line in enumerate(block.lines):
        lw = _line_width(block.font, line)
        if align == "center":
            cx = x + (box_width - lw) / 2
        elif align == "right":
            cx = x + box_width - lw
        else:
            cx = x
        ly = y + i * block.line_height
        for w in line:
            for piece in w:
                txt = piece.text.upper() if uppercase else piece.text
                draw.text((cx, ly + asc), txt, font=block.font, fill=accent if piece.accent else color, anchor="ls")
                cx += block.font.getlength(txt)
            cx += space


@functools.lru_cache(maxsize=256)
def has_glyphs(font_path: str, text: str) -> bool:
    """True if every character of ``text`` exists in the font (not rendered as .notdef)."""
    font = load_font(font_path, 48)
    def sig(ch: str) -> tuple:
        mask = font.getmask(ch)
        return mask.size, bytes(mask)

    notdef = sig("\U0010fffd")
    return all(ch.isspace() or sig(ch) != notdef for ch in text)


SYMBOL_FALLBACKS = ("NotoSansSymbols2-Regular", "NotoSansSymbols-Regular", "NotoSansMath-Regular", "DejaVuSans")


def font_for(text: str, *preferred: str) -> str | None:
    """First font (path) among ``preferred`` + symbol fallbacks that can render ``text``."""
    for cand in preferred:
        if has_glyphs(cand, text):
            return cand
    for name in SYMBOL_FALLBACKS:
        try:
            path = find_font(name)
        except FontNotFound:
            continue
        if has_glyphs(path, text):
            return path
    return None


def hex_to_rgba(color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a = int(c[6:8], 16) if len(c) == 8 else 255
    return r, g, b, int(a * alpha)
