from rich.cells import cell_len

from repetui.controls import ReviewAction, ReviewControls
from repetui.help_text import build_help


def test_every_review_action_is_listed() -> None:
    text = build_help(ReviewControls.defaults(), None).lower()
    for action in ReviewAction:
        assert action.label.lower() in text


def test_rebound_key_is_shown() -> None:
    controls = ReviewControls.defaults().with_binding(ReviewAction.TOGGLE_AUDIO, "w")
    line = next(
        row for row in build_help(controls, None).splitlines() if "toggle audio" in row
    )
    assert line.strip().startswith("w")


def test_unbound_action_shows_unbound() -> None:
    controls = ReviewControls.from_saved({"again": "m"})
    line = next(
        row for row in build_help(controls, None).splitlines() if "toggle audio" in row
    )
    assert "unbound" in line


def test_herdr_section_only_with_keys() -> None:
    assert "herdr" not in build_help(ReviewControls.defaults(), None)
    text = build_help(ReviewControls.defaults(), "bottom=prefix+a;side=prefix+shift+a")
    assert "herdr" in text and "prefix+a" in text and "prefix+shift+a" in text


def test_lines_fit_40_columns() -> None:
    text = build_help(ReviewControls.defaults(), "bottom=prefix+a;side=prefix+shift+a")
    assert max(cell_len(line) for line in text.splitlines()) <= 39


def test_overlong_herdr_key_wraps_instead_of_overflowing() -> None:
    text = build_help(ReviewControls.defaults(), "bottom=prefix+ctrl+shift+alt+backspace")
    assert "prefix+ctrl+shift+alt+backspace" in text
    assert "bottom bar on/off" in text
    assert max(cell_len(line) for line in text.splitlines()) <= 39


def test_settings_section_lists_every_settings_key() -> None:
    text = build_help(ReviewControls.defaults(), None)
    settings = text.split("settings\n", 1)[1]
    for key in ("h / l", "tab", "j / k", "g / G", "space", "enter", "backspace", "esc"):
        assert any(row.strip().startswith(key) for row in settings.splitlines()), key


def test_flag_row_mentions_the_flag_digits_without_changing_the_label() -> None:
    row = next(
        row for row in build_help(ReviewControls.defaults(), None).splitlines()
        if row.strip().startswith("f ")
    )
    assert row.strip() == "f        flag (then 0–7)"
    assert ReviewAction.FLAG.label == "Flag"
