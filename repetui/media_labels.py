"""Neutral, name-free media labels for display; identity labels stay upstream for inference."""

from __future__ import annotations

import re
from collections import Counter

from rich.style import Style
from rich.text import Text

from .card_text import substitute

# Upstream labels never contain "[", "]" or newlines in their names (presentation._label_safe).
MEDIA_LABEL = re.compile(r"\[(audio|image|video|math image)(?:: [^\[\]\n]*)?\]")


def media_kinds(text: str) -> list[str]:
    return [match.group(1) for match in MEDIA_LABEL.finditer(text)]


def neutral_media_labels(
    text: Text, totals: Counter[str] | None = None, seen: Counter[str] | None = None
) -> Text:
    """Replace media labels with `[kind]` / `[kind n]`; share counters to number across texts."""
    if totals is None:
        totals = Counter(kind for kind in media_kinds(text.plain) if kind != "math image")
    if seen is None:
        seen = Counter()

    return substitute(text, MEDIA_LABEL, lambda match: neutral_label(match.group(1), totals, seen))


def neutral_label(kind: str, totals: Counter[str], seen: Counter[str]) -> str:
    """The next display label of `kind` on a side; advances `seen`."""
    if kind == "math image":
        return "[math]"
    seen[kind] += 1
    return f"[{kind} {seen[kind]}]" if totals[kind] > 1 else f"[{kind}]"


def neutral_label_str(value: str) -> str:
    return neutral_media_labels(Text(value)).plain


# Display labels as the card shows them; "[math: x]" (an expression) and notices are not labels.
DISPLAY_LABEL = r"\[(?:(?:audio|image|video)(?: [0-9]+)?|math|media)\]"
_LABELS = rf"(?:{DISPLAY_LABEL}[ \t]*)+"
_END_OF_BLOCK = r"(?=[ \t]*(?:·|\n|$))"
# Applied in order; each also takes what a removed label would leave behind.
_HIDE_STEPS = tuple(re.compile(pattern, re.MULTILINE) for pattern in (
    rf"^[ \t]*{_LABELS}\n",                 # a line holding only labels, with its newline
    rf"\n[ \t]*{_LABELS}$",                 # ... or the last such line, with the newline before
    rf"\[[ \t]*{_LABELS}\]",                # a reading that held only labels
    rf"[ \t]*·[ \t]*{_LABELS}{_END_OF_BLOCK}",  # a " · " block that held only labels
    rf"^{_LABELS}[ \t]*·[ \t]*",            # ... or the first such block
    rf"^{_LABELS}",                         # at the start of a line, with the space after
    rf"[ \t]*{DISPLAY_LABEL}",              # anywhere else, with the space before
))


def _picture_anchors(text: Text) -> list[tuple[int, int]]:
    """Picture labels that a preview will replace (not those inside the larger front)."""
    def meta(span) -> dict:
        style = Style.parse(span.style) if isinstance(span.style, str) else span.style
        return style.meta or {}

    large = [(s.start, s.end) for s in text.spans if meta(s).get("repetui_large_front")]
    return [
        (s.start, s.end) for s in text.spans
        if isinstance(meta(s).get("repetui_image"), str)
        and not any(start <= s.start and s.end <= end for start, end in large)
    ]


def hide_media_labels(text: Text, *, keep_pictures: bool = False) -> Text:
    """Remove media labels from shown text, with the spaces and separators they leave.

    With `keep_pictures`, a label that anchors a picture preview stays, for the preview to
    replace (see images.expand_image_previews(hide_labels=True))."""
    for step in _HIDE_STEPS:
        anchors = _picture_anchors(text) if keep_pictures else []

        def replace(match: re.Match[str], anchors: list[tuple[int, int]] = anchors) -> str:
            if any(start < match.end() and match.start() < end for start, end in anchors):
                return match.group(0)
            return ""

        text = substitute(text, step, replace)
    return text
