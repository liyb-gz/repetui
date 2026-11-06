"""Round 3: media redaction holds by construction (parser structure plus a name scrub)."""

import pytest

from repetui.backend import DueCounts
from repetui.flow import SectionState, compose_review, display_section_name
from repetui.preferences import SectionMode
from repetui.presentation import (
    AVReference,
    CardTemplateIdentity,
    RawCardContent,
    SourceField,
    TemplateFieldProfile,
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


# --- (d) the name scrub ---

CORPUS = [
    # round 1 and round 2 re-review repros
    "<ruby>Q<rt>[sound:SECRET.mp3]</rt></ruby>",
    '<ruby>Q<rt><audio src="SECRET.mp3" title="SECRET.mp3">SECRET.mp3</audio></rt></ruby>',
    '<ruby>Q<rt><video src="SECRET.mp4"><source src="SECRET.webm">SECRET</video></rt></ruby>',
    '<ruby>Q<rt><img src="SECRET.png" alt="SECRET" title="SECRET.png"></rt></ruby>',
    '<ruby>Q<rt><svg><title>SECRET.svg</title><image href="SECRET.svg"/></svg></rt></ruby>',
    # round 3 re-review repros
    '<ruby>Q<rt><svg></bogus><title>SECRET.svg</title><image href="SECRET.svg"/></svg></rt></ruby>',
    '<span hidden></bogus>SECRET.png</span><img src="SECRET.png">',
    '<ruby>Q<rt><math><audio src="SECRET.mp3">SECRET</audio></math></rt></ruby>',
    '<span class="mathjax"><video src="SECRET.mp4">SECRET.mp4</video></span>',
    '<ruby>Q<rt><span class="mathjax"><video src="SECRET.mp4">SECRET</video></span></rt></ruby>',
    "<ruby>Q<rt><pre>よみ</rt></ruby><code>body</code>[sound:SECRET.mp3]",
    '<object data="SECRET.swf">SECRET.swf</object>',
    '<iframe src="SECRET.html">SECRET.html</iframe>',
    '<ruby>Q<rt><object data="SECRET.swf">SECRET</object></rt></ruby>',
    '<span style="display:\n none">SECRET.png</span><img src="SECRET.png">',
    '<span aria-hidden>SECRET.png</span><img src="SECRET.png">',
    # other malformed or hostile shapes
    '<audio src=SECRET.mp3>SECRET fallback',
    '<div hidden><img src="x.png"/>SECRET.png</div><img src="SECRET.png">',
    '<picture><source srcset="SECRET.webp 1x, SECRET-2x.webp 2x"><img src="SECRET.png"></picture>',
    '<video poster="SECRET.jpg" src="clip.mp4"><track label="SECRET" src="SECRET.vtt"></video>',
    "<div style=\"background:url('SECRET.jpg')\">SECRET.jpg</div>",
    '<a href="SECRET.pdf">SECRET.pdf</a>',
    "plain mention of SECRET.mp3 and SECRET [sound:SECRET.mp3]",
    '<svg><image href="SECRET.svg"/><text>SECRET</text></svg>',
    '<math><mi>x</mi><img src="SECRET.png" alt="SECRET.png"></math>',
    "<ruby>Q<rt><code>[sound:SECRET.mp3]</code></rt></ruby>",
    '<embed src="SECRET.swf">SECRET.swf',
    "<ruby>Q<rt>[sound:SECRET.mp3]",
    "<script>SECRET.mp3</script><style>.x{background:url(SECRET.png)}</style>",
    "<template>SECRET</template><noscript>SECRET.mp3</noscript>[sound:SECRET.mp3]",
    '<audio><source src="SECRET.ogg">SECRET.ogg</audio>',
    '<audio src="SECRET%20x.mp3">SECRET x.mp3</audio> SECRET%20x.mp3',
    "[sound:dir/SECRET.mp3] dir/SECRET.mp3",
    "<h2>[sound:SECRET.mp3] SECRET:</h2>body",
    "<b>SECRET.mp3:</b> x [sound:SECRET.mp3]",
    "<u><b>SECRET</b></u> [sound:SECRET.mp3]",
    '<ruby>SECRET<rt>SECRET.mp3</rt></ruby><img src="SECRET.mp3">',
    '<img class="latex" src="SECRET.png">',
    '<img src="data:image/png;base64,SECRETAAAA" alt="x"> [sound:SECRET.mp3]',
    '<img src="http://[broken/SECRET.png"> <a href="http://[x">SECRET.png</a>',
    '<img src="https://cdn.example/SECRET.png?v=2#x">SECRET.png',
]

FORBIDDEN = ("SECRET",)
# Shapes where the card author also prints the bare word as visible text. That word is card content
# (language decks name audio after the answer word), so only file-like forms are forbidden there.
AUTHOR_WORD = {
    "plain mention of SECRET.mp3 and SECRET [sound:SECRET.mp3]",
    "<h2>[sound:SECRET.mp3] SECRET:</h2>body",
    "<u><b>SECRET</b></u> [sound:SECRET.mp3]",
    '<ruby>SECRET<rt>SECRET.mp3</rt></ruby><img src="SECRET.mp3">',
}
FILE_FORMS = ("SECRET.", "SECRET%", "/SECRET")


def _outputs(content: RawCardContent) -> list[str]:
    outputs: list[str] = []
    card = present_card(content)
    shown = display_presentation(card)
    for side in (shown.front, shown.back):
        for section in side.sections:
            outputs += [section.text, section.label or "", section.source_label or ""]
            outputs += [reading for _, _, reading in section.furigana]
    for section in card.back.sections + card.front.sections:
        outputs.append(display_section_name(section))
    states = tuple(
        SectionState(section, SectionMode.FOLD, expanded=True) for section in card.back.sections
    )
    for readings in (True, False):
        for revealed in (True, False):
            outputs.append(compose_review(
                card, "Deck", DueCounts(1, 0, 0), 60, revealed=revealed,
                sections=states if revealed else (), show_readings=readings,
            ).plain)
    return outputs


@pytest.mark.parametrize("shape", CORPUS)
def test_no_media_name_reaches_any_display_output(shape) -> None:
    contents = [
        raw(f"<div>{shape}</div>", f"<div>{shape}</div><hr id=answer><div>A {shape}</div>"),
        raw("<div>q</div>", f"<hr id=answer><h2>Meaning:</h2><div>{shape}</div>"),
    ]
    fields = (SourceField("Front", f"q {shape}"), SourceField("Back", f"a {shape}"))
    contents.append(raw(f"q {shape}", f"<hr id=answer>a {shape}", *fields))
    forbidden = FILE_FORMS if shape in AUTHOR_WORD else FORBIDDEN
    for content in contents:
        for output in _outputs(content):
            for secret in forbidden:
                assert secret not in output, (shape, output)
    # the field-setup preview renders the saved profile
    profile = TemplateFieldProfile(("Front",), ("Back",))
    preview = present_card(raw("x", "<hr id=answer>y", *fields), profile)
    for revealed in (True, False):
        flow = compose_review(preview, "D", DueCounts(1, 0, 0), 60, revealed=revealed).plain
        for secret in forbidden:
            assert secret not in flow, (shape, flow)


def test_scrub_keeps_playback_identities_real() -> None:
    card = present_card(raw(
        '[sound:SECRET.mp3] <img src="SECRET.png"> <ruby>Q<rt>[sound:SECRET-2.mp3]</rt></ruby>',
        "<hr id=answer>a",
        front_av=(AVReference("audio", "SECRET.mp3"),),
    ))
    section = card.front.sections[0]
    assert "[audio: SECRET.mp3]" in section.text
    assert [image.source for image in section.images] == ["SECRET.png"]
    assert [r for _, _, r in section.furigana] == ["[audio: SECRET-2.mp3]"]
    shown = display_presentation(card).front.sections[0]
    assert [image.source for image in shown.images] == ["SECRET.png"]   # v still opens it
    assert shown.text == "[audio 1] [image] Q"
    assert [r for _, _, r in shown.furigana] == ["[audio 2]"]


def test_scrub_never_touches_ordinary_words_or_short_stems() -> None:
    shown = display_presentation(present_card(raw(
        '<img src="cat.png"> cat and image and audio <a href="https://x.org/wiki/Elephant">'
        "Elephant</a> [sound:post.mp3] posted",
        "<hr id=answer>a",
    ))).front
    assert shown.text == "[image] cat and image and audio Elephant [audio] posted"


def test_scrub_replaces_a_leaked_file_name_but_keeps_the_bare_word() -> None:
    shown = display_presentation(present_card(raw(
        "see SECRET.mp3 or SECRET [sound:SECRET.mp3]", "<hr id=answer>a"
    ))).front
    assert shown.text == "see [media] or SECRET [audio]"


def test_answer_word_named_like_its_audio_still_shows() -> None:
    # Language decks often name the audio after the answer word: the word itself must stay.
    shown = display_presentation(present_card(raw(
        "<div>Q</div>", "<hr id=answer><div>hallo [sound:hallo.mp3]</div>"
    ))).back
    assert "hallo [audio]" in "".join(section.text for section in shown.sections)


# --- round 4: false positives, gaps, nested math ---

def _front(front: str) -> str:
    return display_presentation(present_card(raw(front, "<hr id=answer>a"))).front.text


@pytest.mark.parametrize("html", [
    "<pre>data = 3.5</pre> e.g. 3.5",
    '<pre>data = "e.g."</pre> e.g.',
    '<code>src="notes.png"</code> notes.png',
    '<a href="https://example.org">https://example.org</a>',
    '<a href="/3.5">3.5</a> and 3.5',
    '<a href="https://example.org/">see example.org</a>',
    '<a href="https://example.org/docs/v2.0">v2.0</a> version 2.0',
    '<a href="https://example.org/page.html">page.html</a>',
])
def test_text_without_a_media_reference_renders_unchanged(html) -> None:
    from repetui.presentation import _render_document

    assert " ".join(_front(html).split()) == " ".join(_render_document(html).text.split())
    assert "[media]" not in _front(html)


def test_ordinary_text_is_never_scrubbed() -> None:
    text = (
        "Visit https://example.org/a/b?x=1 or www.test.com. Version 3.5.1 and v2.0, e.g. 3.5 "
        "kg. Mr. Smith wrote notes.txt, photo.png and song.mp3 (not on this card)."
    )
    assert _front(f"{text} [sound:other.mp3]") == f"{text} [audio]"


def test_language_card_keeps_the_visible_word_named_like_its_audio() -> None:
    shown = display_presentation(present_card(raw(
        "<div>hallo</div>[sound:hallo.mp3]", "<hr id=answer><div>hallo means hello</div>"
    )))
    assert shown.front.text.split() == ["hallo", "[audio]"]
    assert "hallo means hello" in shown.back.text and "[media]" not in shown.back.text


def test_query_and_fragment_never_hide_a_linked_file_name() -> None:
    assert _front('<a href="SECRET.pdf?download=1">SECRET.pdf</a>') == "[media]"
    assert _front('<img src="pics/SECRET.png#v2"> SECRET.png') == "[image] [media]"
    assert _front('<img src="https://cdn.example/x/SECRET.png?s=2">see SECRET.png') == (
        "[image]see [media]")


@pytest.mark.parametrize("html", [
    '<img src="SECRET  NAME.png"> SECRET  NAME.png',
    '<img src="SECRET NAME.png"> SECRET&nbsp;NAME.png',
    '<img src="SECRET&nbsp;NAME.png"> SECRET NAME.png',
    '<img src="SECRET%20NAME.png"> SECRET%2520NAME.png',
    '<img src="SECRET_NAME.png"> SECRET NAME.png',
])
def test_whitespace_and_encoding_variants_of_a_name_are_scrubbed(html) -> None:
    assert "SECRET" not in _front(html)


@pytest.mark.parametrize("readings", [True, False])
def test_normalised_name_inside_a_reading_is_scrubbed(readings) -> None:
    card = present_card(raw(
        '<img src="SECRET  NAME.png"><ruby>Q<rt>SECRET  NAME.png</rt></ruby>', "<hr id=answer>a"
    ))
    shown = display_presentation(card).front.sections[0]
    assert "SECRET" not in " ".join(r for _, _, r in shown.furigana)
    flow = compose_review(card, "D", DueCounts(1, 0, 0), 60, revealed=False,
                          show_readings=readings).plain
    assert "SECRET" not in flow


def test_settings_label_never_shows_a_transformed_file_name() -> None:
    card = present_card(raw(
        "q", "<hr id=answer><h2>SECRET_FILE.mp3</h2>body [sound:SECRET_FILE.mp3]"
    ))
    assert [display_section_name(section) for section in card.back.sections][0] == "[media]"
