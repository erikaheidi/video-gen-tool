from explainer.render.layout import find_font, font_for, has_glyphs, layout_text, parse_rich, wrap, load_font

FONT = find_font("NotoSans-Regular")


def test_parse_rich_accents():
    (words,) = parse_rich("Ship *fast er* now")
    assert [[(p.text, p.accent) for p in w] for w in words] == [
        [("Ship", False)], [("fast", True)], [("er", True)], [("now", False)]]


def test_wrap_respects_width_and_newlines():
    font = load_font(FONT, 40)
    lines, too_wide = wrap("one two three four five six seven\nnext", font, 300)
    assert not too_wide and len(lines) >= 3
    assert "".join(p.text for p in lines[-1][0]) == "next"


def test_autofit_shrinks_to_fit():
    text = "a fairly long sentence that will not fit in a tiny box at the requested size"
    block = layout_text(text, FONT, 80, 400, max_height=300)
    assert block.size < 80 and block.height <= 300 and not block.overflow


def test_overflow_flagged_at_min_size():
    block = layout_text("word " * 200, FONT, 60, 300, max_height=100)
    assert block.overflow


def test_glyph_detection_and_fallback():
    assert has_glyphs(FONT, "Hello")
    assert not has_glyphs(find_font("NotoSans-ExtraBold"), "→")
    assert font_for("→", find_font("NotoSans-ExtraBold")) is not None
