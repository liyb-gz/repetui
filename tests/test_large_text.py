from __future__ import annotations

import pytest
from rich.text import Text

from repetui import large_text
from repetui.large_text import (
    SCALES,
    fonts_available,
    next_scale,
    placeholder,
    render_large_text,
    scale_label,
)

needs_fonts = pytest.mark.skipif(not fonts_available(), reason="macOS system fonts")
BG = (30, 30, 30)
CELL = (16.0, 32.0)


def test_z_cycles_normal_then_one_and_a_half_then_two_then_three() -> None:
    seen = [1.0]
    for _ in SCALES:
        seen.append(next_scale(seen[-1]))
    assert seen == [1.0, 1.5, 2.0, 3.0, 1.0]
    assert [scale_label(s) for s in SCALES] == ["normal", "1.5×", "2×", "3×"]
    assert next_scale(7.0) == 1.5                       # an unknown saved value restarts the cycle


def test_normal_size_and_empty_text_draw_nothing() -> None:
    assert render_large_text(Text("소금"), 1.0, 40, 10, CELL, background=BG) is None
    assert render_large_text(Text("   "), 2.0, 40, 10, CELL, background=BG) is None


@needs_fonts
@pytest.mark.parametrize("scale,rows", [(1.5, 2), (2.0, 2), (3.0, 3)])
def test_one_short_line_takes_scale_rows_rounded_up(scale, rows) -> None:
    drawn = render_large_text(Text("소금은 바닷물처럼"), scale, 60, 10, CELL, background=BG)
    assert drawn is not None and (drawn.columns, drawn.rows) == (60, rows)
    assert drawn.image.size == (60 * 16, rows * 32)         # exactly the block, at the cell size


@needs_fonts
def test_long_text_wraps_by_word_and_reports_its_rows() -> None:
    sentence = "소금은 바닷물처럼 짠맛이에요. " * 4
    drawn = render_large_text(Text(sentence), 2.0, 30, 40, CELL, background=BG)
    assert drawn is not None and drawn.rows >= 6 and drawn.rows % 2 == 0


@needs_fonts
def test_too_tall_for_the_pane_falls_back() -> None:
    sentence = "소금은 바닷물처럼 짠맛이에요. " * 8
    assert render_large_text(Text(sentence), 3.0, 30, 6, CELL, background=BG) is None


@needs_fonts
def test_a_word_wider_than_the_block_breaks_between_characters() -> None:
    drawn = render_large_text(Text("가" * 40), 3.0, 20, 40, CELL, background=BG)
    assert drawn is not None and drawn.rows > 3


@needs_fonts
def test_colours_and_bold_reach_the_picture() -> None:
    text = Text("소금 ")
    text.append("처럼", style="bold #dd7744")
    drawn = render_large_text(text, 2.0, 40, 10, CELL, background=BG)
    assert drawn is not None
    colours = {colour for _, colour in drawn.image.getcolors(maxcolors=1 << 16)}
    assert any(abs(r - 0xdd) < 8 and abs(g - 0x77) < 8 and abs(b - 0x44) < 8 for r, g, b in colours)
    assert BG in colours


@needs_fonts
def test_small_reported_cells_are_drawn_at_twice_the_size() -> None:
    drawn = render_large_text(Text("abc"), 2.0, 10, 10, (8.0, 16.0), background=BG)
    assert drawn is not None and drawn.image.size == (10 * 16, drawn.rows * 32)


@needs_fonts
def test_unknown_cell_size_still_draws() -> None:
    drawn = render_large_text(Text("Ĉu vi parolas? Привіт"), 2.0, 40, 10, None, background=BG)
    assert drawn is not None and drawn.rows == 2


def test_each_script_gets_a_font_that_has_it() -> None:
    assert large_text._family("ㅗ") == "cjk" and large_text._family("소") == "cjk"
    assert large_text._family("ĉ") == "latin" and large_text._family("Ж") == "latin"
    assert large_text._family("ก") == "wide"


def test_the_placeholder_is_blank_and_exactly_the_block() -> None:
    block = placeholder(10, 4)
    lines = block.plain.split("\n")
    assert len(lines) == 4 and all(line == " " * 10 for line in lines)
