"""Help text generated from the live review key bindings."""

from __future__ import annotations

from rich.cells import cell_len

from .controls import ReviewAction, ReviewControls

_WIDTH = 9
_MAX_COLUMNS = 40


def _line(key: str, label: str) -> str:
    """Return one indented help row, wrapping the label below a very wide key."""
    gap = max(_WIDTH - cell_len(key), 1)
    row = f"  {key}{' ' * gap}{label}"
    if cell_len(row) <= _MAX_COLUMNS:
        return row
    return f"  {key}\n  {' ' * _WIDTH}{label}"


def _parse_herdr_keys(raw: str | None) -> dict[str, str]:
    keys: dict[str, str] = {}
    for part in (raw or "").split(";"):
        name, _, key = part.partition("=")
        if name.strip() and key.strip():
            keys[name.strip()] = key.strip()
    return keys


def build_help(controls: ReviewControls, herdr_keys: str | None) -> str:
    def key(action: ReviewAction) -> str:
        return controls.binding(action) or "unbound"

    lines = [
        "everywhere",
        _line("?", "settings and help"),
        _line("n", "read notification"),
        _line("ctrl+l", "redraw screen"),
        _line("q", "quit"),
        "",
        "decks",
        _line("j / k", "move"),
        _line("tab", "expand / collapse"),
        _line("enter", "review"),
        _line(key(ReviewAction.SYNC), "sync"),
        _line(key(ReviewAction.MEDIA_PROGRESS), "media progress"),
        "",
        "review",
        _line("space", "reveal / open fold"),
        _line("j / k", "scroll, select folds"),
        _line("g / G", "top / bottom"),
        _line("esc", "decks"),
    ]
    lines += [_line(key(action), action.label.lower()) for action in ReviewAction]
    lines += [
        "",
        "settings",
        _line("h / l", "previous / next tab"),
        _line("j / k", "select / scroll"),
        _line("enter", "change / rebind"),
        _line("esc", "return"),
    ]
    herdr = _parse_herdr_keys(herdr_keys)
    if herdr:
        lines += [
            "",
            "herdr",
            _line(herdr.get("bottom", "unbound"), "bottom bar on/off"),
            _line(herdr.get("side", "unbound"), "sidebar on/off"),
            "  closing the bar keeps repetui running",
            "  q in repetui quits it",
        ]
    return "\n".join(lines)
