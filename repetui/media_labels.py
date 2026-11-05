"""Neutral, name-free media labels for display; identity labels stay upstream for inference."""

from __future__ import annotations

import re
from collections import Counter

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

    def replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        if kind == "math image":
            return "[math]"
        seen[kind] += 1
        return f"[{kind} {seen[kind]}]" if totals[kind] > 1 else f"[{kind}]"

    return substitute(text, MEDIA_LABEL, replace)


def neutral_label_str(value: str) -> str:
    return neutral_media_labels(Text(value)).plain
