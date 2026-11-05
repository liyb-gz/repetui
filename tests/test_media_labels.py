from rich.style import Style
from rich.text import Text

from repetui.media_labels import neutral_label_str, neutral_media_labels


def test_single_labels_lose_names() -> None:
    out = neutral_media_labels(Text("Q [image: pic secret.png] [audio: secret.mp3] [video: s.mp4]"))
    assert out.plain == "Q [image] [audio] [video]"


def test_repeated_kinds_are_numbered_in_order() -> None:
    out = neutral_media_labels(Text("[audio: a.mp3] x [image: i.png] [audio: b.mp3]"))
    assert out.plain == "[audio 1] x [image] [audio 2]"


def test_latex_image_shows_math_but_real_math_is_kept() -> None:
    out = neutral_media_labels(Text("[math image: latex-ANSWER.png] and [math: x^2]"))
    assert out.plain == "[math] and [math: x^2]"


def test_bare_labels_are_counted_too() -> None:
    assert neutral_media_labels(Text("[image] [image: b.png]")).plain == "[image 1] [image 2]"


def test_shared_counters_number_across_texts() -> None:
    from collections import Counter
    totals, seen = Counter({"audio": 2}), Counter()
    first = neutral_media_labels(Text("[audio: a.mp3]"), totals, seen).plain
    second = neutral_media_labels(Text("[audio: b.mp3]"), totals, seen).plain
    assert (first, second) == ("[audio 1]", "[audio 2]")


def test_image_meta_survives_redaction() -> None:
    text = Text("see ")
    text.append("[image: secret.png]", Style(meta={"repetui_image": "secret.png"}))
    out = neutral_media_labels(text)
    sources = [s.style.meta.get("repetui_image") for s in out.spans if isinstance(s.style, Style)]
    assert out.plain == "see [image]" and "secret.png" in sources


def test_text_to_speech_label_is_left_alone() -> None:
    assert neutral_label_str("[text to speech: ja_JP]") == "[text to speech: ja_JP]"
