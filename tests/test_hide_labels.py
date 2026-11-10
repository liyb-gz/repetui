import pytest
from rich.style import Style
from rich.text import Text

from repetui.images import expand_image_previews
from repetui.large_text import LARGE_FRONT_META
from repetui.media_labels import hide_media_labels


@pytest.mark.parametrize("shown,hidden", [
    ("박히다[audio]", "박히다"),
    ("word [audio]", "word"),
    ("[audio] word", "word"),
    ("a [audio 1] b [audio 2] c", "a b c"),
    ("Q[[audio]] rest", "Q rest"),                       # a reading that held only a label
    ("word · [image]", "word"),
    ("[image] · word", "word"),
    ("line one\n[audio]\nline two", "line one\nline two"),
    ("line one\n[image 1] [image 2]\nline two", "line one\nline two"),
    ("see [media] here", "see here"),
    ("[video] and [math]", "and"),
    ("[math: x^2] stays", "[math: x^2] stays"),          # an expression is content, not a label
    ("[picture unavailable]", "[picture unavailable]"),  # a notice, not a label
    ("[audiobook] [type answer]", "[audiobook] [type answer]"),
])
def test_labels_and_what_they_leave_behind_disappear(shown, hidden) -> None:
    assert hide_media_labels(Text(shown)).plain == hidden


def test_marks_on_the_remaining_text_survive() -> None:
    text = Text("bold [audio] end")
    text.stylize("bold", 0, 4)
    text.stylize("italic", 13, 16)
    hidden = hide_media_labels(text)
    assert hidden.plain == "bold end"
    marks = [(s.start, s.end, str(s.style)) for s in hidden.spans]
    assert marks == [(0, 4, "bold"), (5, 8, "italic")]


def _picture(label: str = "[image]", source: str = "cat.png") -> Text:
    text = Text("before ")
    text.append(label, style=Style(meta={"repetui_image": source}))
    text.append(" after")
    return text


def test_a_picture_anchor_stays_when_pictures_will_replace_it() -> None:
    assert hide_media_labels(_picture(), keep_pictures=True).plain == "before [image] after"
    assert hide_media_labels(_picture(), keep_pictures=False).plain == "before after"


def test_an_anchor_inside_the_larger_front_is_not_kept() -> None:
    text = _picture()
    text.stylize(Style(meta={LARGE_FRONT_META: True}), 0, len(text))
    assert hide_media_labels(text, keep_pictures=True).plain == "before after"


def test_previews_replace_their_label_when_labels_are_hidden(tmp_path) -> None:
    from PIL import Image

    Image.new("RGB", (16, 16), "#b73524").save(tmp_path / "cat.png")
    shown = expand_image_previews(_picture(), lambda name: tmp_path / name, 20, 2)
    hidden = expand_image_previews(_picture(), lambda name: tmp_path / name, 20, 2,
                                   hide_labels=True)
    assert "[image]" in shown.plain
    assert "[image]" not in hidden.plain and hidden.plain.startswith("before ")
    assert len(hidden.plain.splitlines()) == len(shown.plain.splitlines())
