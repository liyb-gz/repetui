"""The front side drawn larger, as a picture for the native (Kitty graphics) overlay.

A terminal program can't change its font size, but it can place a picture over a block of cells.
The front's display text (already redacted) is laid out at a larger size with system fonts and
drawn into exactly that block. The cells underneath keep the normal-size text, so a terminal that
ignores the picture still shows the card.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.cells import cell_len
from rich.color import Color
from rich.style import Style
from rich.text import Text

SCALES = (1.0, 1.5, 2.0, 3.0)
LARGE_FRONT_META = "repetui_large_front"  # flow tags the front with this when it is drawn larger
_LABELS = {1.0: "normal", 1.5: "1.5×", 2.0: "2×", 3.0: "3×"}

# macOS system fonts: Hangul/CJK, then Latin/Greek/Cyrillic, then a wide-coverage fallback.
_FONTS = {
    "cjk": (Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"), {False: 0, True: 6}),
    "latin": (Path("/System/Library/Fonts/Helvetica.ttc"), {False: 0, True: 1}),
    "wide": (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), {False: 0, True: 0}),
}
_ITALIC_LATIN = {False: 2, True: 3}  # Helvetica Oblique / Bold Oblique
_CJK_RANGES = (
    (0x1100, 0x11FF), (0x2E80, 0x303F), (0x3040, 0x30FF), (0x3130, 0x318F), (0x31F0, 0x31FF),
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xA960, 0xA97F), (0xAC00, 0xD7AF), (0xD7B0, 0xD7FF),
    (0xF900, 0xFAFF), (0xFF00, 0xFFEF),
)
_LATIN_RANGES = ((0x0000, 0x052F), (0x1E00, 0x1FFF), (0x2000, 0x20CF), (0x2100, 0x214F))
_GLYPH_HEIGHT = 0.78   # font size as a share of the line height (a terminal row at 1×)
_BASELINE = 0.8        # baseline position within a line
_UNKNOWN_CELL = (16.0, 32.0)


def next_scale(scale: float) -> float:
    index = SCALES.index(scale) if scale in SCALES else 0
    return SCALES[(index + 1) % len(SCALES)]


def scale_label(scale: float) -> str:
    return _LABELS.get(scale, "normal")


@dataclass(frozen=True)
class LargeText:
    image: Image.Image
    columns: int
    rows: int


def _family(char: str) -> str:
    point = ord(char)
    if any(low <= point <= high for low, high in _CJK_RANGES):
        return "cjk"
    if any(low <= point <= high for low, high in _LATIN_RANGES):
        return "latin"
    return "wide"


@lru_cache(maxsize=64)
def _font(family: str, bold: bool, italic: bool, size: int) -> ImageFont.FreeTypeFont:
    path, faces = _FONTS[family]
    index = _ITALIC_LATIN[bold] if family == "latin" and italic else faces[bold]
    return ImageFont.truetype(str(path), size, index=index)


def fonts_available() -> bool:
    return all(path.is_file() for path, _ in _FONTS.values())


def _styles(text: Text) -> list[Style]:
    """The effective Rich style of every character."""
    base = Style.parse(text.style) if isinstance(text.style, str) else (text.style or Style())
    styles = [base] * len(text.plain)
    for span in text.spans:
        style = Style.parse(span.style) if isinstance(span.style, str) else span.style
        for index in range(max(span.start, 0), min(span.end, len(styles))):
            styles[index] = styles[index] + style
    return styles


def _rgb(color: Color | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if color is None or color.is_default:
        return default
    triplet = color.get_truecolor()
    return (triplet.red, triplet.green, triplet.blue)


@dataclass(frozen=True)
class _Run:
    text: str
    family: str
    bold: bool
    italic: bool
    underline: bool
    color: tuple[int, int, int]


def _runs(chars: Iterable[tuple[str, Style]], default: tuple[int, int, int]) -> list[_Run]:
    runs: list[_Run] = []
    for char, style in chars:
        run = _Run(char, _family(char), bool(style.bold), bool(style.italic),
                   bool(style.underline), _rgb(style.color, default))
        last = runs[-1] if runs else None
        key = (run.family, run.bold, run.italic, run.underline, run.color)
        if last is not None and key == (last.family, last.bold, last.italic, last.underline,
                                        last.color):
            runs[-1] = _Run(last.text + char, last.family, last.bold, last.italic,
                            last.underline, last.color)
        else:
            runs.append(run)
    return runs


def _width(runs: list[_Run], size: int) -> float:
    return sum(_font(r.family, r.bold, r.italic, size).getlength(r.text) for r in runs)


def _wrap(line: list[tuple[str, Style]], max_width: float, size: int,
          default: tuple[int, int, int]) -> list[list[tuple[str, Style]]]:
    """Greedy word wrap of one hard line; a word wider than a line breaks between characters."""
    def fits(items: list[tuple[str, Style]]) -> bool:
        return _width(_runs(items, default), size) <= max_width

    tokens: list[list[tuple[str, Style]]] = []       # alternating runs of spaces and of non-spaces
    for item in line:
        if tokens and (item[0] == " ") == (tokens[-1][-1][0] == " "):
            tokens[-1].append(item)
        else:
            tokens.append([item])
    lines: list[list[tuple[str, Style]]] = [[]]
    for token in tokens:
        if token[0][0] == " ":
            if lines[-1]:
                lines[-1] += token                      # trimmed if the line ends here
            continue
        if fits(lines[-1] + token):
            lines[-1] += token
            continue
        if any(char != " " for char, _ in lines[-1]):
            lines.append([])
        else:
            lines[-1] = []
        for item in token:
            if lines[-1] and not fits(lines[-1] + [item]):
                lines.append([])
            lines[-1].append(item)
    for wrapped in lines:
        while wrapped and wrapped[-1][0] == " ":
            wrapped.pop()
    return lines


def render_large_text(
    text: Text,
    scale: float,
    columns: int,
    max_rows: int,
    cell: tuple[float, float] | None,
    *,
    background: tuple[int, int, int],
    foreground: tuple[int, int, int] = (238, 233, 224),
) -> LargeText | None:
    """Draw `text` at `scale` into a block `columns` wide; None if it can't (or doesn't fit)."""
    if scale <= 1 or columns < 1 or max_rows < 1 or not text.plain.strip() or not fonts_available():
        return None
    cell_w, cell_h = cell or _UNKNOWN_CELL
    if cell_h < 24:                       # render small cells at twice the size, for sharpness
        cell_w, cell_h = cell_w * 2, cell_h * 2
    line_h = scale * cell_h
    size = max(8, round(line_h * _GLYPH_HEIGHT))
    max_width = columns * cell_w
    styles = _styles(text)
    lines: list[list[tuple[str, Style]] | None] = []
    for hard in _split_lines(list(zip(text.plain, styles, strict=True))):
        if not hard:
            lines.append(None)            # a blank line between blocks: half a line
            continue
        lines.extend(_wrap(hard, max_width, size, foreground))
    height = sum(line_h / 2 if line is None else line_h for line in lines)
    rows = math.ceil(height / cell_h - 1e-6)
    if rows > max_rows:
        return None
    image = Image.new("RGB", (round(columns * cell_w), round(rows * cell_h)), background)
    draw = ImageDraw.Draw(image)
    y = 0.0
    for line in lines:
        if line is None:
            y += line_h / 2
            continue
        x = 0.0
        baseline = y + line_h * _BASELINE
        for run in _runs(line, foreground):
            font = _font(run.family, run.bold, run.italic, size)
            draw.text((x, baseline), run.text, font=font, fill=run.color, anchor="ls")
            advance = font.getlength(run.text)
            if run.underline:
                thickness = max(1, round(size / 14))
                top = baseline + size * 0.1
                draw.rectangle((x, top, x + advance, top + thickness), fill=run.color)
            x += advance
        y += line_h
    return LargeText(image, columns, rows)


def _split_lines(chars: list[tuple[str, Style]]) -> list[list[tuple[str, Style]]]:
    lines: list[list[tuple[str, Style]]] = [[]]
    for char, style in chars:
        if char == "\n":
            lines.append([])
        else:
            lines[-1].append((char, style))
    while lines and not lines[-1]:
        lines.pop()
    # collapse runs of blank lines into one
    collapsed: list[list[tuple[str, Style]]] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return collapsed


def placeholder(text: Text, columns: int, rows: int, style: str | Style) -> Text:
    """The cells under the picture: the normal-size text, wrapped and padded to the block."""
    lines: list[str] = []
    for hard in text.plain.split("\n"):
        current = ""
        for char in hard:
            if cell_len(current + char) > columns:
                lines.append(current)
                current = ""
            current += char
        lines.append(current)
    lines = (lines + [""] * rows)[:rows]
    block = Text("\n".join(line + " " * (columns - cell_len(line)) for line in lines), style=style)
    block.overflow = "fold"
    return block


def expand_large_front(
    document: Text,
    scale: float,
    width: int,
    max_rows: int,
    cell: tuple[float, float] | None,
    background: tuple[int, int, int],
    native_marks: list[tuple],
) -> tuple[Text, bool]:
    """Swap the tagged front for a block of cells and queue its picture in `native_marks`.

    Returns the document and whether the front was drawn larger. Without a tagged front, or when
    it can't be drawn, the document comes back with the front as normal text."""
    starts = [span.start for span in document.spans if _meta(span.style).get(LARGE_FRONT_META)]
    ends = [span.end for span in document.spans if _meta(span.style).get(LARGE_FRONT_META)]
    if not starts:
        return document, False
    start, end = min(starts), max(ends)
    front = document[start:end]
    drawn = render_large_text(front, scale, width, max_rows, cell, background=background)
    if drawn is None:
        return document, False
    block = placeholder(front, drawn.columns, drawn.rows, "bold #eee9e0")
    block.stylize(Style(meta={"repetui_native_picture": len(native_marks)}), 0, len(block))
    native_marks.append((None, drawn.columns, drawn.rows, drawn.image))
    result = document[:start]
    result.overflow = document.overflow or "fold"
    result.append_text(block)
    result.append_text(document[end:])
    return result, True


def _meta(style: str | Style) -> dict:
    return (Style.parse(style) if isinstance(style, str) else style).meta or {}
