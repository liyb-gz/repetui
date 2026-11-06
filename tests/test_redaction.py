"""Round 3: media redaction holds by construction in the parser structure."""

import pytest

from repetui.presentation import (
    CardTemplateIdentity,
    RawCardContent,
    SourceField,
    _render_document,
    display_presentation,
    present_card,
)

IDENTITY = CardTemplateIdentity(31, "Redaction", 0, "Card 1")


def raw(front: str, back: str, *fields: SourceField, **av) -> RawCardContent:
    return RawCardContent(IDENTITY, front, back, fields, **av)


def reading_of(html: str) -> list[str]:
    return [reading for _, _, reading in _render_document(html).furigana]


# --- (a) suppression is a tag-name stack ---

def test_unmatched_end_tag_never_ends_svg_suppression_inside_a_reading() -> None:
    doc = _render_document("<ruby>Q<rt><svg></bogus><title>PRIVATE</title></svg></rt></ruby>")
    assert doc.text == "Q" and reading_of(
        "<ruby>Q<rt><svg></bogus><title>PRIVATE</title></svg></rt></ruby>") == ["[image]"]


@pytest.mark.parametrize("html", [
    "<span hidden></bogus>PRIVATE</span>after",
    "<div style='display:none'></p></span></li>PRIVATE</div>after",
    "<script></b>PRIVATE</script>after",
    "<span hidden><b></i></b>PRIVATE</span>after",
])
def test_unmatched_end_tags_never_end_suppression(html) -> None:
    assert _render_document(html).text == "after"


def test_an_ancestor_end_tag_still_closes_suppression() -> None:
    assert _render_document("<div><span hidden>PRIVATE</div>after").text == "after"


# --- (b) media under math is recognised ---

def test_audio_inside_math_inside_a_reading_is_audio_not_math() -> None:
    html = '<ruby>Q<rt><math><audio src="a.mp3">PRIVATE</audio></math></rt></ruby>'
    assert reading_of(html) == ["[audio: a.mp3]"]
    shown = display_presentation(present_card(raw(html, "<hr id=answer>x")))
    assert [r for _, _, r in shown.front.sections[0].furigana] == ["[audio]"]


@pytest.mark.parametrize("wrapper", [
    '<span class="mathjax">{}</span>', "<anki-mathjax>{}</anki-mathjax>", "<math>{}</math>",
])
def test_video_inside_a_math_wrapper_is_video(wrapper) -> None:
    body = _render_document(wrapper.format('<video src="v.mp4">PRIVATE</video>'))
    assert body.text == "[video: v.mp4]"
    rt = reading_of("<ruby>Q<rt>" + wrapper.format('<video src="v.mp4">PRIVATE</video>')
                    + "</rt></ruby>")
    assert rt == ["[video: v.mp4]"]


def test_real_math_keeps_its_expression() -> None:
    assert _render_document("<anki-mathjax>x^2</anki-mathjax>").text == "[math: x^2]"
    assert _render_document("<math><mi>x</mi><mo>+</mo><mn>1</mn></math>").text == "[math: x+1]"
    assert _render_document('<span class="mathjax">\\(a\\)</span> b').text == "[math: \\(a\\)] b"


# --- (c) a reading restores the parser state it began with ---

def test_unclosed_pre_inside_a_reading_does_not_change_the_body() -> None:
    doc = _render_document("<ruby>Q<rt><pre>よみ</rt></ruby><code>body</code>")
    assert doc.text == "Q`body`"


@pytest.mark.parametrize("inner", ["<pre>", "<code>", "<h2>", "<u>", "<table><tr><td>", "<li>"])
def test_unclosed_structure_inside_a_reading_ends_with_it(inner) -> None:
    doc = _render_document(f"<ruby>Q<rt>{inner}よみ</rt></ruby> tail <code>x</code>")
    assert doc.text == "Q tail `x`" and not doc.underlines
    assert [section for section in doc.structural_sections] == []


# --- (e) pre-existing parser defects ---

@pytest.mark.parametrize("html", [
    '<object data="x.swf" type="application/x-shockwave-flash">FALLBACK</object>after',
    '<iframe src="page.html">FALLBACK</iframe>after',
    '<noscript>FALLBACK</noscript>after',
])
def test_object_and_iframe_fallback_never_render(html) -> None:
    assert _render_document(html).text == "after"
    assert reading_of(f"<ruby>Q<rt>よ{html}</rt></ruby>") == ["よafter"]


@pytest.mark.parametrize("style", [
    "display:\n none", "display : none", "DISPLAY:\tNONE", "visibility:\n  hidden",
    "color: red;\n display:\r\nnone ;",
])
def test_hidden_css_with_whitespace_still_hides(style) -> None:
    assert _render_document(f'<span style="{style}">PRIVATE</span>after').text == "after"


def test_bare_aria_hidden_hides_and_false_does_not() -> None:
    assert _render_document("<span aria-hidden>PRIVATE</span>after").text == "after"
    assert _render_document('<span aria-hidden="">PRIVATE</span>after').text == "after"
    assert _render_document('<span aria-hidden="false">shown</span>').text == "shown"


def test_bare_attributes_never_raise() -> None:
    assert _render_document("<input id class>x<img src alt>").text == "x[image]"
