"""Lossless terminal-native presentation of rendered Anki cards.

The module is deliberately pure: callers provide rendered card content and receive
immutable sections. Ordinary cards display their rendered sides. When dynamic markup
cannot be separated confidently, an inferred profile may use only source fields whose
content is demonstrably present on that rendered side; users can then confirm or replace
the field mapping explicitly.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import quote

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

from .card_text import (
    INLINE_FURIGANA,
    annotated,
    annotations,
    extract_readings,
    normalise,
    substitute,
)
from .media_labels import (
    MEDIA_LABEL,
    media_kinds,
    neutral_label,
    neutral_label_str,
    neutral_media_labels,
)
from .media_names import media_names, scrub, scrub_text

_ANSWER_RULE = re.compile(r'<hr[^>]*\bid\s*=\s*["\']?answer["\']?[^>]*>', re.IGNORECASE)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_CLASS = re.compile(r"(?:[a-z][\w-]*)?\.([\w-]+)", re.IGNORECASE)
_FURIGANA_HINT = re.compile(r"(?:\[[ぁ-んァ-ンー]{1,30}\]|（[ぁ-んァ-ンー]{1,30}）)")
_MACHINE_VALUE = re.compile(
    r"(?:https?://\S+|[0-9]{3,}|[0-9a-f]{12,}(?::[0-9]+)?)",
    re.IGNORECASE,
)
_UNDERLINED_HEADING = re.compile(
    r"<u\b[^>]*>\s*(?:<span\b[^>]*>\s*)?"
    r"<(?P<tag>b|strong)\b[^>]*>(?P<label>[^<>]{1,80})</(?P=tag)>"
    r"(?:\s*</span>)?\s*</u>",
    re.IGNORECASE,
)
_INLINE_LABEL = re.compile(
    r"<(?P<tag>b|strong)\b[^>]*>\s*(?P<label>[^<>:\r\n]{1,80}):\s*</(?P=tag)>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CardTemplateIdentity:
    """Stable identity for one Anki card template."""

    note_type_id: int
    note_type_name: str
    template_ordinal: int
    template_name: str


@dataclass(frozen=True)
class SourceField:
    """A note field available for section identification or profile display."""

    name: str
    html: str


@dataclass(frozen=True)
class TemplateFieldProfile:
    """Ordered source fields used for a terminal-native card presentation."""

    prompt_fields: tuple[str, ...]
    answer_fields: tuple[str, ...]
    ignored_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldLayoutSuggestion:
    """A conservative multi-card proposal and the evidence behind it."""

    profile: TemplateFieldProfile | None
    confidence: Literal["high", "low"]
    reasons: tuple[str, ...]
    unresolved_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AVReference:
    """Meaningful fallback for one indexed rendered Anki AV marker."""

    kind: str
    label: str | None = None

    @property
    def placeholder(self) -> str:
        if self.kind == "audio" and self.label:
            label = _source_id(self.label)
        else:
            label = _label_safe(self.label or "")
        return f"[{self.kind}: {label}]" if label else f"[{self.kind}]"


@dataclass(frozen=True)
class RawCardContent:
    """Rendered Anki payload accepted at the presentation seam."""

    identity: CardTemplateIdentity
    front_html: str
    back_html: str
    fields: tuple[SourceField, ...] = ()
    front_av: tuple[AVReference, ...] = ()
    back_av: tuple[AVReference, ...] = ()
    card_css: str = ""


@dataclass(frozen=True)
class ImageSpan:
    """One image occurrence in a section's normalized text."""

    start: int
    end: int
    source: str


@dataclass(frozen=True)
class PresentationSection:
    """One ordered section whose content came from the rendered card side."""

    id: str
    text: str
    label: str | None = None
    source_label: str | None = None
    label_is_content: bool = False
    underlines: tuple[tuple[int, int], ...] = ()
    furigana: tuple[tuple[int, int, str], ...] = ()
    images: tuple[ImageSpan, ...] = ()
    # Every media name the card's raw HTML references; display redaction scrubs them (media_names).
    hidden_names: frozenset[str] = dataclass_field(default=frozenset(), compare=False, repr=False)

    @property
    def display_text(self) -> str:
        if not self.label_is_content:
            return self.text
        if self.label and self.text:
            return f"{self.label}\n{self.text}"
        return self.label or self.text


@dataclass(frozen=True)
class CardSide:
    """All visible content for one side in display order."""

    sections: tuple[PresentationSection, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(section.display_text for section in self.sections)

    @property
    def display_width(self) -> int:
        return max((cell_len(line) for line in self.text.splitlines()), default=0)


@dataclass(frozen=True)
class CardPresentation:
    identity: CardTemplateIdentity
    front: CardSide
    back: CardSide
    suggested_profile: TemplateFieldProfile | None = None


@dataclass
class _RawSection:
    kind: str
    start: int
    label_end: int
    end: int


@dataclass
class _MediaCapture:
    """One open <audio>/<video> element: its identity is every source inside it."""

    kind: str
    sources: list[str] = dataclass_field(default_factory=list)


@dataclass
class _RubyCapture:
    base_start: int
    rt_base_end: int = 0
    reading_parts: list[str] = dataclass_field(default_factory=list)


@dataclass
class _Frame:
    """One open element and the one effect it started; closing it undoes exactly that.

    Effects: "suppress" (hidden, script, svg, object ...: nothing inside is shown), "media"
    (<audio>/<video>: only source identities are collected), "math" (content buffered, then shown
    as [math: ...] unless it is only media), "rt" (a reading: content buffered into it), "ruby",
    "rp", "pre", "heading", "label", or "" (no effect).
    """

    tag: str
    effect: str = ""
    underlined: bool = False
    close_text: str = ""
    ruby: _RubyCapture | None = None
    sink: list[str] | None = None


_ONLY_MEDIA = re.compile(r"(?:\s*\[(?:audio|image|video|math image)(?:: [^\[\]\n]*)?\])+\s*")


class _RenderedHTMLParser(HTMLParser):
    """Render readable text while retaining safe structural section hints.

    All parser state lives on one stack of open elements (`_Frame`). An end tag closes only a
    matching open element (and anything left open inside it); an unmatched end tag closes
    nothing, so it can never end suppression, a reading or math early. Content is the same
    wherever it appears: inside a reading or math it takes exactly the body's path, and only the
    output is buffered (`_sinks`).
    """

    _BLOCKS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "main",
        "nav",
        "ol",
        "p",
        "section",
        "table",
        "ul",
    }
    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    # Never rendered, or rendered only when the real content cannot be (fallback content).
    _SUPPRESSED = {"script", "style", "template", "object", "iframe", "noscript", "title"}
    _STRUCTURE = {"ruby", "rt", "rp", "repetui-label"} | _HEADINGS
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "wbr",
    }

    def __init__(
        self,
        underline_classes: frozenset[str] = frozenset(),
        av: tuple[AVReference, ...] = (),
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._av = av
        self.parts: list[str] = []
        self.sections: list[_RawSection] = []
        self._active: _RawSection | None = None
        self._stack: list[_Frame] = []
        self._effects: Counter[str] = Counter()
        self._underline_depth = 0
        self._sinks: list[list[str]] = []
        self._media: _MediaCapture | None = None
        self._reading: _RubyCapture | None = None
        self._ruby_stack: list[_RubyCapture] = []
        self._row_cells = 0
        self._underline_classes = underline_classes
        self._raw_length = 0
        self.underline_ranges: list[tuple[int, int]] = []
        self.ruby_ranges: list[tuple[int, int, str]] = []
        self.image_ranges: list[ImageSpan] = []

    def _emit(self, text: str) -> None:
        if self._sinks:
            self._sinks[-1].append(text)  # buffered into the open reading or math
            return
        start = self._raw_length
        self._raw_length += len(text)
        if self._underline_depth and text:
            if self.underline_ranges and self.underline_ranges[-1][1] == start:
                previous, _ = self.underline_ranges[-1]
                self.underline_ranges[-1] = (previous, self._raw_length)
            else:
                self.underline_ranges.append((start, self._raw_length))
        self.parts.append(text)
        if self._active is not None:
            self._active.end = self._raw_length
            if self._effects["heading"] or self._effects["label"]:
                self._active.label_end = self._raw_length

    @staticmethod
    def _classes(attributes: dict[str, str | None]) -> set[str]:
        return set((attributes.get("class") or "").lower().split())

    @staticmethod
    def _is_hidden(attributes: dict[str, str | None]) -> bool:
        style = re.sub(r"\s+", "", attributes.get("style") or "").casefold()
        classes = set((attributes.get("class") or "").casefold().split())
        aria = attributes.get("aria-hidden")
        return (
            "hidden" in attributes
            or "hidden" in classes
            # a bare aria-hidden (or any value but "false") hides, as browsers treat it
            or ("aria-hidden" in attributes and (aria or "").strip().casefold() != "false")
            or "display:none" in style
            or "visibility:hidden" in style
        )

    def _push(self, frame: _Frame) -> None:
        self._stack.append(frame)
        self._effects[frame.effect] += 1
        self._underline_depth += int(frame.underlined)
        if frame.sink is not None:
            self._sinks.append(frame.sink)

    def _pop(self) -> None:
        frame = self._stack.pop()
        self._effects[frame.effect] -= 1
        self._underline_depth -= int(frame.underlined)
        if frame.sink is not None:
            self._sinks.pop()
        if frame.effect == "media":
            self._flush_media()
        elif frame.effect == "math":
            content = "".join(frame.sink or ())
            # Media inside math is media: never wrap its label (or anything) as an expression.
            self._emit(content.strip() if _ONLY_MEDIA.fullmatch(content) else f"[math: {content}]")
        elif frame.effect == "rt" and frame.ruby is not None:
            self._reading = None
            self._finish_reading(frame.ruby)
        elif frame.effect == "ruby" and self._ruby_stack:
            self._ruby_stack.pop()
        if frame.close_text:
            self._emit(frame.close_text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        frame = None if tag in self._VOID else _Frame(tag)
        if self._effects["media"]:
            # Nothing inside a media element is shown; nested sources join its identity.
            if self._media is not None and tag in {"audio", "video", "source"} and (
                source := attributes.get("src")
            ):
                self._media.sources.append(_source_id(source))
            if frame is not None:
                self._push(frame)
            return
        if self._effects["suppress"] or (self._reading is None and self._effects["rp"]):
            if frame is not None:
                self._push(frame)
            return
        if tag in self._SUPPRESSED or self._is_hidden(attributes):
            if frame is not None:
                frame.effect = "suppress"
                self._push(frame)
            return
        if self._sinks and tag in self._STRUCTURE:
            if frame is not None:
                self._push(frame)  # structure inside a reading or math is only text
            return
        ruby = self._ruby_stack[-1] if self._ruby_stack else None
        if tag == "ruby" and frame is not None:
            capture = _RubyCapture(self._raw_length)
            self._ruby_stack.append(capture)
            frame.effect, frame.ruby = "ruby", capture
            self._push(frame)
            return
        if ruby is not None and tag == "rt" and frame is not None:
            ruby.rt_base_end = self._raw_length
            ruby.reading_parts = []
            frame.effect, frame.ruby, frame.sink = "rt", ruby, ruby.reading_parts
            self._reading = ruby
            self._push(frame)
            return
        if ruby is not None and tag == "rp" and frame is not None:
            frame.effect = "rp"
            self._push(frame)
            return

        classes = self._classes(attributes)
        if frame is not None:
            frame.underlined = (
                tag in {"u", "ins"}
                or _declares_underline(attributes.get("style") or "") is True
                or bool(classes & self._underline_classes)
            )
        if tag == "svg" and frame is not None:
            self._emit("[math]" if "latex" in classes else "[image]")
            frame.effect = "suppress"
        elif tag in self._HEADINGS and frame is not None:
            self._emit("\n")
            self._active = _RawSection(
                "heading", self._raw_length, self._raw_length, self._raw_length
            )
            self.sections.append(self._active)
            frame.effect, frame.close_text = "heading", "\n"
        elif tag == "repetui-label" and frame is not None:
            self._active = _RawSection(
                "label", self._raw_length, self._raw_length, self._raw_length
            )
            self.sections.append(self._active)
            frame.effect = "label"
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._emit("\n────────\n")
        elif tag == "li":
            self._emit("\n• ")
        elif tag == "tr" and frame is not None:
            self._row_cells = 0
            self._emit("\n")
            frame.close_text = "\n"
        elif tag in {"td", "th"}:
            if self._row_cells:
                self._emit(" │ ")
            self._row_cells += 1
        elif tag == "rt" and frame is not None:
            self._emit("（")
            frame.close_text = "）"
        elif tag == "pre" and frame is not None:
            self._emit("\n```text\n")
            frame.effect, frame.close_text = "pre", "\n```\n"
        elif tag == "code" and frame is not None and not self._effects["pre"]:
            self._emit("`")
            frame.close_text = "`"
        elif frame is not None and (tag in {"math", "anki-mathjax"} or "mathjax" in classes):
            frame.effect, frame.sink = "math", []
        elif tag == "img":
            # Identity is the full source only; alt/title never decide sameness.
            label = _source_id(attributes.get("src") or "")
            kind = "math image" if "latex" in classes else "image"
            start = self._raw_length
            self._emit(f"[{kind}: {label}]" if label else f"[{kind}]")
            if kind == "image" and not self._sinks:  # a reading or math has no picture
                self.image_ranges.append(
                    ImageSpan(start, self._raw_length, attributes.get("src") or "")
                )
        elif tag in {"audio", "video"} and frame is not None:
            source = attributes.get("src")
            self._media = _MediaCapture(tag, [_source_id(source)] if source else [])
            frame.effect = "media"
        elif tag == "input" and (
            (attributes.get("id") or "").lower() == "typeans" or "typeans" in classes
        ):
            self._emit("[type answer]")
        elif tag in self._BLOCKS and frame is not None:
            self._emit("\n")
            frame.close_text = "\n"
        if frame is not None:
            self._push(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<img/>` is only a start tag; `<span/>` opens and closes.
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                break
        else:
            return  # unmatched (</bogus>, </br>, a stray </p>): closes nothing
        while len(self._stack) > index:
            self._pop()  # the element, and anything still open inside it

    def _flush_media(self) -> None:
        media, self._media = self._media, None
        if media is not None:
            label = " ".join(media.sources)
            self._emit(f"[{media.kind}: {label}]" if label else f"[{media.kind}]")

    def _finish_reading(self, ruby: _RubyCapture) -> None:
        reading = " ".join(_normalise("".join(ruby.reading_parts), self._av).split())
        if reading and ruby.base_start < ruby.rt_base_end:
            self.ruby_ranges.append((ruby.base_start, ruby.rt_base_end, reading))
        elif reading:
            self._emit(f"（{reading}）")
        ruby.base_start = self._raw_length

    def close(self) -> None:
        super().close()
        while self._stack:
            self._pop()  # unclosed elements end at the document end

    def handle_data(self, data: str) -> None:
        if self._effects["media"] or self._effects["suppress"]:
            return  # media fallback and suppressed content are never shown
        if self._reading is None and self._effects["rp"]:
            return
        self._emit(data)


class _ClassCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.classes: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.classes.update((dict(attrs).get("class") or "").casefold().split())


def _html_classes(html: str) -> frozenset[str]:
    parser = _ClassCollector()
    parser.feed(html)
    return frozenset(parser.classes)


@dataclass(frozen=True)
class _RenderedDocument:
    text: str
    structural_sections: tuple[tuple[str, Text, Text], ...]
    prelude: Text
    atoms: tuple[str, ...]
    underlines: tuple[tuple[int, int], ...] = ()
    furigana: tuple[tuple[int, int, str], ...] = ()
    images: tuple[ImageSpan, ...] = ()


def _declares_underline(declarations: str) -> bool | None:
    result: bool | None = None
    for declaration in declarations.split(";"):
        name, separator, value = declaration.partition(":")
        if separator and name.strip().casefold() in {
            "text-decoration",
            "text-decoration-line",
        }:
            result = bool(re.search(r"\bunderline\b", value, re.IGNORECASE))
    return result


def _underlined_classes(css: str) -> frozenset[str]:
    """Recognize only simple class selectors with explicit underline rules."""
    result: set[str] = set()
    for selectors, declarations in _CSS_RULE.findall(_CSS_COMMENT.sub("", css)):
        underlined = _declares_underline(declarations)
        if underlined is None:
            continue
        for selector in selectors.split(","):
            match = _SIMPLE_CLASS.fullmatch(selector.strip())
            if match:
                name = match.group(1).casefold()
                if underlined:
                    result.add(name)
                else:
                    result.discard(name)
    return frozenset(result)


def _without_inline_furigana(text: str) -> str:
    return INLINE_FURIGANA.sub(lambda match: match.group("base"), text)


class _StartTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), dict(attrs)))


def _suppresses(fragment: str) -> bool:
    """Does any start tag in `fragment` hide its content (hidden, aria-hidden, display:none...)?"""
    collector = _StartTags()
    collector.feed(fragment)
    collector.close()
    return any(
        tag in _RenderedHTMLParser._SUPPRESSED or _RenderedHTMLParser._is_hidden(attributes)
        for tag, attributes in collector.tags
    )


def _promote_structural_labels(html: str) -> str:
    """Rewrite visible bold/underlined labels as structural tags. A label whose markup hides it
    is left as it is, so the rewrite can never drop a suppression attribute."""

    def promote(match: re.Match[str], promoted: str) -> str:
        return match.group() if _suppresses(match.group()) else promoted

    html = _UNDERLINED_HEADING.sub(
        lambda match: promote(match, f"<h6>{match.group('label')}</h6>"), html
    )
    return _INLINE_LABEL.sub(
        lambda match: promote(
            match, f"<repetui-label>{match.group('label')}:</repetui-label>"
        ),
        html,
    )


def _render_document(
    html: str,
    av: tuple[AVReference, ...] = (),
    *,
    card_css: str = "",
) -> _RenderedDocument:
    parser = _RenderedHTMLParser(_underlined_classes(card_css), av)
    parser.feed(_promote_structural_labels(html))
    parser.close()
    raw_text = "".join(parser.parts)
    placeholders = tuple(reference.placeholder for reference in av)

    def render_range(start: int, end: int) -> Text:
        source = annotated(
            raw_text[start:end],
            tuple(
                (max(left, start) - start, min(right, end) - start)
                for left, right in parser.underline_ranges if left < end and right > start
            ),
            tuple(
                (max(left, start) - start, min(right, end) - start, reading)
                for left, right, reading in parser.ruby_ranges if left < end and right > start
            ),
        )
        for image in parser.image_ranges:
            if image.start >= start and image.end <= end:
                source.stylize(
                    Style(meta={"repetui_image": image.source}),
                    image.start - start,
                    image.end - start,
                )
        return extract_readings(normalise(source, placeholders))

    marked = render_range(0, len(raw_text))
    text = marked.plain
    underlines, furigana = annotations(marked)
    images = _image_spans(marked)
    sections = tuple(
        (
            section.kind,
            render_range(section.start, section.label_end),
            render_range(section.label_end, section.end),
        )
        for section in parser.sections
    )
    atoms = tuple(
        atom
        for part in parser.parts
        if (atom := _without_inline_furigana(_normalise(part, av)))
    )
    return _RenderedDocument(
        text,
        sections,
        render_range(0, parser.sections[0].start if parser.sections else len(raw_text)),
        atoms,
        underlines,
        furigana,
        images,
    )


def _normalise(text: str, av: tuple[AVReference, ...] = ()) -> str:
    return normalise(annotated(text), tuple(reference.placeholder for reference in av)).plain


def _reconcile_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _slug(value: str) -> str:
    slug = "".join(character if character.isalnum() else "-" for character in value.casefold())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "section"


def _label_key(value: str) -> str:
    words = "".join(character if character.isalnum() else " " for character in value.casefold())
    return " ".join(words.split())


def _source_id(value: str) -> str:
    """Injective, label-safe identity for a media source: percent-encode brackets, %, controls."""
    return quote(value, safe="/:._-~()+,;=&'!$*@")


def _label_safe(value: str) -> str:
    """Human text (alt/title) inside labels can never contain brackets or line breaks."""
    return re.sub(r"[\[\]\r\n\v\f\x1c\x1d\x1e\x07\x08\x85\u2028\u2029]+", " ", value).strip()


def _image_spans(text: Text) -> tuple[ImageSpan, ...]:
    images: list[ImageSpan] = []
    for span in text.spans:
        style = Style.parse(span.style) if isinstance(span.style, str) else span.style
        source = style.meta.get("repetui_image")
        if isinstance(source, str) and span.start < span.end:
            images.append(ImageSpan(span.start, span.end, source))
    return tuple(images)


def _marked_text(
    text: str,
    underlines: tuple[tuple[int, int], ...],
    furigana: tuple[tuple[int, int, str], ...],
    images: tuple[ImageSpan, ...],
) -> Text:
    result = annotated(text, underlines, furigana)
    for image in images:
        result.stylize(
            Style(meta={"repetui_image": image.source}), image.start, image.end
        )
    return result


def _label_repeats_text(section: PresentationSection) -> bool:
    """A `Label: body` section whose label is also the start of its text."""
    return bool(section.label) and not section.label_is_content and (
        section.text.casefold().startswith(f"{section.label}:".casefold())
    )


def _visible_text(section: PresentationSection) -> str:
    """Label and text in display order, each visible media occurrence once."""
    if section.label and not section.label_is_content and not _label_repeats_text(section):
        return f"{section.label}\n{section.text}"
    return section.display_text


def _shown_readings(section: PresentationSection) -> list[tuple[int, str]]:
    """Distinct (end, reading) pairs in display order: a reading shows right after its base."""
    return sorted({(end, reading) for _, end, reading in section.furigana})


def _relabel(text: str, labels: list[str]) -> str:
    return MEDIA_LABEL.sub(_relabel_match(labels), text)


def _neutral_text(
    section: PresentationSection, totals: Counter[str], seen: Counter[str]
) -> Text:
    """Redact a section's text and its readings in one pass, numbered in display order."""
    shown = _shown_readings(section)
    # (display position, 0 = reading / 1 = body, reading index, offset, kind); a reading shows at
    # its base's end, before any body label that starts there.
    slots = [(m.start(), 1, -1, m.start(), m.group(1)) for m in MEDIA_LABEL.finditer(section.text)]
    slots += [
        (end, 0, index, m.start(), m.group(1))
        for index, (end, reading) in enumerate(shown)
        for m in MEDIA_LABEL.finditer(reading)
    ]
    labels = {slot: neutral_label(slot[4], totals, seen) for slot in sorted(slots)}
    names = section.hidden_names
    readings = {
        key: scrub(_relabel(key[1], [labels[slot] for slot in slots if slot[1:3] == (0, index)]),
                   names)
        for index, key in enumerate(shown)
    }
    furigana = tuple(
        (start, end, readings[(end, reading)]) for start, end, reading in section.furigana
    )
    marked = _marked_text(section.text, section.underlines, furigana, section.images)
    marked = substitute(
        marked, MEDIA_LABEL, _relabel_match([labels[slot] for slot in slots if slot[1] == 1])
    )
    return scrub_text(marked, names)  # last: a name the parser let through becomes [media]


def _relabel_match(labels: list[str]) -> Callable[[re.Match[str]], str]:
    """Replace successive matches with `labels` in order."""
    replacement = iter(labels)
    return lambda _match: next(replacement)


def neutral_side(sections: tuple[PresentationSection, ...]) -> tuple[PresentationSection, ...]:
    """Redact one side for display, numbering each media kind across the whole side, readings
    included (they are display text too: media inside <rt> shows only its neutral label)."""
    totals: Counter[str] = Counter()
    for section in sections:
        visible = [_visible_text(section)] + [r for _, r in _shown_readings(section)]
        totals.update(
            kind for text in visible for kind in media_kinds(text) if kind != "math image"
        )
    seen: Counter[str] = Counter()
    shown: list[PresentationSection] = []
    for section in sections:
        label = section.label
        names = section.hidden_names
        if label and _label_repeats_text(section):
            # Same numbers as the text's own copy of the label, so prefix stripping still works.
            label = scrub(neutral_media_labels(Text(label), totals, seen.copy()).plain, names)
        elif label:
            label = scrub(neutral_media_labels(Text(label), totals, seen).plain, names)
        source_label = (
            scrub(neutral_label_str(section.source_label), names) if section.source_label else None
        )
        marked = _neutral_text(section, totals, seen)
        underlines, furigana = annotations(marked)
        shown.append(replace(
            section, text=marked.plain, label=label, source_label=source_label,
            underlines=underlines, furigana=furigana, images=_image_spans(marked),
        ))
    return tuple(shown)


def display_presentation(presentation: CardPresentation) -> CardPresentation:
    return replace(
        presentation,
        front=CardSide(neutral_side(presentation.front.sections)),
        back=CardSide(neutral_side(presentation.back.sections)),
    )


def _unique_id(prefix: str, value: str, seen: dict[str, int]) -> str:
    base = f"{prefix}:{_slug(value)}"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}:{seen[base]}"


def _with_document_marks(
    section: PresentationSection,
    document: _RenderedDocument,
    offset: int,
) -> PresentationSection:
    if not section.text:
        return section
    if document.text[offset : offset + len(section.text)] != section.text:
        return section
    end = offset + len(section.text)
    marked = _marked_text(
        document.text, document.underlines, document.furigana, document.images
    )[offset:end]
    underlines, furigana = annotations(marked)
    return replace(
        section, underlines=underlines, furigana=furigana, images=_image_spans(marked)
    )


def _structural_sections(side: str, document: _RenderedDocument) -> tuple[PresentationSection, ...]:
    if not document.structural_sections:
        return ()
    seen: dict[str, int] = {}
    result: list[PresentationSection] = []
    if document.prelude:
        underlines, furigana = annotations(document.prelude)
        result.append(PresentationSection(
            f"{side}:preamble", document.prelude.plain,
            underlines=underlines, furigana=furigana,
            images=_image_spans(document.prelude),
        ))
    for kind, raw_label, body in document.structural_sections:
        if kind == "heading" and _image_spans(raw_label):
            # A heading image cannot live in a section's plain-text label.
            return ()
        label = raw_label.plain.removesuffix(":").strip()
        if label or body:
            if kind == "label" or raw_label.plain.rstrip().endswith(":"):
                text = normalise(Text(" ").join((raw_label, body)))
                underlines, furigana = annotations(text)
                result.append(
                    PresentationSection(
                        _unique_id(f"{side}:label", label, seen),
                        text.plain,
                        label=label or None,
                        underlines=underlines,
                        furigana=furigana,
                        images=_image_spans(text),
                    )
                )
            else:
                underlines, furigana = annotations(body)
                result.append(
                    PresentationSection(
                        _unique_id(f"{side}:heading", label, seen),
                        body.plain,
                        label=label or None,
                        label_is_content=True,
                        underlines=underlines,
                        furigana=furigana,
                        images=_image_spans(body),
                    )
                )
    joined = "\n\n".join(section.display_text for section in result)
    if _reconcile_key(joined) != _reconcile_key(document.text):
        return ()
    return tuple(result)


def _field_sections(
    side: str, document: _RenderedDocument, fields: tuple[SourceField, ...]
) -> tuple[PresentationSection, ...]:
    matches: list[tuple[int, int, str]] = []
    for field in fields:
        field_text = _render_document(field.html).text
        if not field_text:
            continue
        start = document.text.find(field_text)
        if start >= 0 and document.text.find(field_text, start + 1) < 0:
            matches.append((start, start + len(field_text), field.name))
    matches.sort()
    if len(matches) < 2:
        return ()
    if any(
        current[0] < previous[1] for previous, current in zip(matches, matches[1:], strict=False)
    ):
        return ()
    cursor = 0
    for start, end, _ in matches:
        if document.text[cursor:start].strip():
            return ()
        cursor = end
    if document.text[cursor:].strip():
        return ()

    seen: dict[str, int] = {}
    sections = tuple(
        PresentationSection(
            _unique_id(f"{side}:field", name, seen),
            text,
            source_label=name,
        )
        for start, end, name in matches
        for text in (document.text[start:end],)
    )
    joined = "\n\n".join(section.display_text for section in sections)
    return (
        tuple(
            _with_document_marks(section, document, start)
            for section, (start, _end, _name) in zip(sections, matches, strict=True)
        )
        if _reconcile_key(joined) == _reconcile_key(document.text)
        else ()
    )


def _with_source_labels(
    sections: tuple[PresentationSection, ...], fields: tuple[SourceField, ...]
) -> tuple[PresentationSection, ...]:
    rendered_fields = [(field.name, _render_document(field.html).text) for field in fields]
    result: list[PresentationSection] = []
    for section in sections:
        source_label = next(
            (
                name
                for name, text in rendered_fields
                if text
                and (
                    _reconcile_key(text) == _reconcile_key(section.text)
                    or (section.label and _label_key(name) == _label_key(section.label))
                )
            ),
            None,
        )
        result.append(replace(section, source_label=source_label))
    return tuple(result)


def _present_side(
    side: str,
    html: str,
    fields: tuple[SourceField, ...],
    av: tuple[AVReference, ...],
) -> CardSide:
    document = _render_document(html, av)
    if not document.text:
        label = "Question" if side == "front" else "Answer"
        return CardSide((PresentationSection(f"{side}:fallback", "(empty card)", label),))

    sections = _structural_sections(side, document)
    if sections:
        return CardSide(_with_source_labels(sections, fields))
    sections = _field_sections(side, document, fields)
    if sections:
        return CardSide(sections)

    label = "Question" if side == "front" else "Answer"
    return CardSide(
        (
            PresentationSection(
                f"{side}:fallback",
                document.text,
                label,
                underlines=document.underlines,
                furigana=document.furigana,
                images=document.images,
            ),
        )
    )


def _strip_answer_html(back_html: str) -> tuple[str, bool]:
    match = _ANSWER_RULE.search(back_html)
    if match:
        return back_html[match.end() :], True
    return back_html, False


def _marked_side_text(side: CardSide) -> Text:
    parts: list[Text] = []
    for section in side.sections:
        body = _marked_text(
            section.text, section.underlines, section.furigana, section.images
        )
        if section.label_is_content and section.label:
            prefix = section.label + ("\n" if section.text else "")
            body = Text(prefix) + body
        parts.append(body)
    return Text("\n\n").join(parts)


def _strip_plain_front(back: CardSide, front: CardSide) -> CardSide:
    front_text = front.text
    back_text = back.text
    if len(back.sections) > 1 and back.sections[0].display_text == front_text:
        return CardSide(back.sections[1:])
    if back_text.startswith(front_text) and (
        len(back_text) == len(front_text)
        or back_text[len(front_text)].isspace()
        or back_text[len(front_text)] == "─"
    ):
        remainder = back_text[len(front_text) :].lstrip("\n ─")
        if remainder:
            offset = len(back_text) - len(remainder)
            marked = _marked_side_text(back)[offset:]
            underlines, furigana = annotations(marked)
            return CardSide((PresentationSection(
                "back:fallback", remainder, "Answer",
                underlines=underlines, furigana=furigana,
                images=_image_spans(marked),
            ),))
    return back


def _field_is_present(field: _RenderedDocument, side: _RenderedDocument) -> bool:
    key = _reconcile_key(field.text)
    if len(key) <= 2 and key.isascii() and key.isalnum():
        return any(_reconcile_key(atom) == key for atom in side.atoms)
    return key in _reconcile_key(side.text)


def _suggestion_key(text: str) -> str:
    """Compare field and rendered-side text despite common ruby/furigana forms."""
    without_readings = _FURIGANA_HINT.sub("", text)
    return re.sub(r"\s+", "", without_readings).casefold()


def _suggestion_position(field: _RenderedDocument, side: _RenderedDocument) -> int:
    key = _suggestion_key(field.text)
    if not key:
        return -1
    if (
        len(key) <= 2
        and key.isascii()
        and key.isalnum()
        and not any(_suggestion_key(atom) == key for atom in side.atoms)
    ):
        return -1
    return _suggestion_key(side.text).find(key)


def _metadata_hint(name: str) -> bool:
    return name.casefold() in {
        "key",
        "kind",
        "after",
        "id",
        "pointid",
        "releaseday",
        "releasedate",
        "sourceurl",
        "url",
        "timestamp",
    } or name.endswith(("ID", "Id", "URL", "Url", "Timestamp"))


def _metadata_evidence(name: str, values: Sequence[str]) -> bool:
    populated = [value for value in values if value]
    return _metadata_hint(name) and bool(populated) and (
        len(set(populated)) == 1
        or all(_MACHINE_VALUE.fullmatch(value) for value in populated)
    )


def suggest_field_layout(samples: Sequence[RawCardContent]) -> FieldLayoutSuggestion:
    """Propose only fields supported by repeated rendered-side evidence.

    Every sample must belong to one note type/template and expose the same field
    names. Unresolved fields remain Auto rather than being silently discarded.
    """
    if not samples:
        return FieldLayoutSuggestion(None, "low", ("More cards are needed to compare.",))
    first = samples[0]
    identity = (first.identity.note_type_id, first.identity.template_ordinal)
    names = tuple(field.name for field in first.fields)
    if any(
        (sample.identity.note_type_id, sample.identity.template_ordinal) != identity
        or tuple(field.name for field in sample.fields) != names
        for sample in samples
    ):
        raise ValueError("Samples must use the same template and source fields.")
    if len(samples) < 2:
        return FieldLayoutSuggestion(
            None, "low", ("More cards are needed for a reliable layout suggestion.",), names
        )
    if len({tuple(field.html for field in sample.fields) for sample in samples}) < 2:
        return FieldLayoutSuggestion(
            None,
            "low",
            ("More varied cards are needed for a reliable layout suggestion.",),
            names,
        )

    values: dict[str, list[str]] = {name: [] for name in names}
    front_positions: dict[str, list[int]] = {name: [] for name in names}
    back_positions: dict[str, list[int]] = {name: [] for name in names}
    back_only_markup_hits: dict[str, int] = {name: 0 for name in names}
    for sample in samples:
        front = _render_document(sample.front_html, sample.front_av)
        back = _render_document(sample.back_html, sample.back_av)
        front_classes = _html_classes(sample.front_html)
        back_classes = _html_classes(sample.back_html)
        for field in sample.fields:
            rendered = _render_document(field.html)
            values[field.name].append(_suggestion_key(rendered.text))
            if not rendered.text:
                continue
            classes = _html_classes(field.html)
            if classes and classes <= back_classes and classes.isdisjoint(front_classes):
                back_only_markup_hits[field.name] += 1
            front_position = _suggestion_position(rendered, front)
            back_position = _suggestion_position(rendered, back)
            if front_position >= 0:
                front_positions[field.name].append(front_position)
            if back_position >= 0:
                back_positions[field.name].append(back_position)

    duplicates: set[str] = set()
    ambiguous_duplicates: set[str] = set()
    split_prompt: set[str] = set()
    split_answer: set[str] = set()
    by_values: dict[tuple[str, ...], list[str]] = {}
    for name in names:
        if any(values[name]):
            by_values.setdefault(tuple(values[name]), []).append(name)
    for group in by_values.values():
        if len(group) < 2:
            continue
        if len(group) == 2:
            back_styled = [
                name
                for name in group
                if back_only_markup_hits[name] == sum(bool(value) for value in values[name])
                and back_only_markup_hits[name] >= 2
            ]
            if len(back_styled) == 1:
                back_name = back_styled[0]
                front_name = next(name for name in group if name != back_name)
                if not _metadata_hint(front_name) and len(front_positions[front_name]) >= 2:
                    split_prompt.add(front_name)
                    split_answer.add(back_name)
                    continue
        if len(
            {
                tuple(sample.fields[names.index(name)].html for sample in samples)
                for name in group
            }
        ) > 1:
            ambiguous_duplicates.update(group)
            continue
        preferred = min(
            group,
            key=lambda name: (
                _metadata_hint(name), names.index(name)
            ),
        )
        duplicates.update(name for name in group if name != preferred)

    prompt: list[str] = []
    answer: list[str] = []
    ignored: list[str] = []
    unresolved: list[str] = []
    for name in names:
        populated = sum(bool(value) for value in values[name])
        front_hits = len(front_positions[name])
        back_hits = len(back_positions[name])
        if name in duplicates:
            ignored.append(name)
        elif name in ambiguous_duplicates or populated == 0:
            unresolved.append(name)
        elif name in split_prompt:
            prompt.append(name)
        elif name in split_answer:
            answer.append(name)
        elif front_hits == populated and front_hits >= 2:
            prompt.append(name)
        elif _metadata_evidence(name, values[name]) and front_hits == 0:
            ignored.append(name)
        elif back_hits == populated and front_hits == 0 and back_hits >= 2:
            answer.append(name)
        else:
            unresolved.append(name)

    varied_prompt = [name for name in prompt if len(set(values[name])) > 1]
    unresolved.extend(name for name in prompt if name not in varied_prompt)
    prompt = varied_prompt

    if not prompt or not answer:
        return FieldLayoutSuggestion(
            None,
            "low",
            ("No clear prompt and answer fields were confirmed across the cards.",),
            tuple(unresolved),
        )
    if len(unresolved) > max(3, len(names) // 3):
        return FieldLayoutSuggestion(
            None,
            "low",
            ("Too many fields could not be classified safely; keep editing manually.",),
            tuple(unresolved),
        )

    def order(name: str, positions: dict[str, list[int]]) -> tuple[float, int]:
        hits = positions[name]
        return sum(hits) / len(hits), names.index(name)

    prompt.sort(key=lambda name: order(name, front_positions))
    answer.sort(key=lambda name: order(name, back_positions))
    reasons = (
        f"Compared {len(samples)} cards from this template.",
        f"Matched {len(prompt)} prompt and {len(answer)} answer fields to rendered sides.",
    )
    if unresolved:
        reasons += (f"Left {len(unresolved)} uncertain fields on Auto.",)
    return FieldLayoutSuggestion(
        TemplateFieldProfile(tuple(prompt), tuple(answer), tuple(ignored)),
        "high",
        reasons,
        tuple(unresolved),
    )


def _shared_word_ratio(front: str, back: str) -> float:
    front_words = set(re.findall(r"\w+", front.casefold()))
    if not front_words:
        return 0.0
    back_words = set(re.findall(r"\w+", back.casefold()))
    return len(front_words & back_words) / len(front_words)


def _suggest_field_profile(
    raw: RawCardContent,
    presentation: CardPresentation,
) -> TemplateFieldProfile | None:
    if len(raw.fields) < 3:
        return None
    if not all(
        len(side.sections) == 1 and side.sections[0].id.endswith(":fallback")
        for side in (presentation.front, presentation.back)
    ):
        return None
    if _ANSWER_RULE.search(raw.back_html) or raw.back_html.startswith(raw.front_html):
        return None
    if not re.search(r"<(?:script|details)\b", raw.front_html + raw.back_html, re.IGNORECASE):
        return None

    front = _render_document(raw.front_html, raw.front_av)
    back = _render_document(raw.back_html, raw.back_av)
    if _shared_word_ratio(front.text, back.text) < 0.5:
        return None
    unique_fields: list[tuple[SourceField, _RenderedDocument]] = []
    seen_content: set[str] = set()
    for field in raw.fields:
        rendered = _render_document(field.html)
        content_key = _reconcile_key(rendered.text)
        if not content_key or content_key in seen_content:
            continue
        seen_content.add(content_key)
        unique_fields.append((field, rendered))

    prompt = next(
        (
            (field, rendered)
            for field, rendered in unique_fields
            if _field_is_present(rendered, front)
        ),
        None,
    )
    if prompt is None:
        return None
    prompt_key = _reconcile_key(prompt[1].text)
    answer_fields = tuple(
        field.name
        for field, rendered in unique_fields
        if _reconcile_key(rendered.text) != prompt_key
        and _field_is_present(rendered, back)
    )
    if not answer_fields:
        return None
    return TemplateFieldProfile((prompt[0].name,), answer_fields)


def _field_side(
    side: str,
    names: tuple[str, ...],
    fields: tuple[SourceField, ...],
    *,
    auto_names: frozenset[str] = frozenset(),
    rendered_side: _RenderedDocument | None = None,
    excluded_auto_content: frozenset[str] = frozenset(),
    card_css: str = "",
) -> CardSide:
    available = {field.name: field for field in fields}
    seen: dict[str, int] = {}
    seen_auto_content = set(excluded_auto_content)
    sections: list[PresentationSection] = []
    for name in names:
        field = available.get(name)
        if field is None:
            continue
        rendered = _render_document(field.html, card_css=card_css)
        text = rendered.text
        if not text:
            continue
        content_key = _reconcile_key(text)
        if name in auto_names:
            if rendered_side is None or not _field_is_present(rendered, rendered_side):
                continue
            if content_key in seen_auto_content:
                continue
        seen_auto_content.add(content_key)
        sections.append(
            PresentationSection(
                _unique_id(f"{side}:field", name, seen),
                text,
                source_label=name,
                underlines=rendered.underlines,
                furigana=rendered.furigana,
                images=rendered.images,
            )
        )
    if sections:
        return CardSide(tuple(sections))
    label = "Question" if side == "front" else "Answer"
    return CardSide((PresentationSection(f"{side}:fallback", "(empty card)", label),))


def _present_fields(
    raw: RawCardContent,
    profile: TemplateFieldProfile,
    *,
    suggested: bool = False,
) -> CardPresentation:
    front = _field_side(
        "front", profile.prompt_fields, raw.fields, card_css=raw.card_css
    )
    assigned = set(
        profile.prompt_fields + profile.answer_fields + profile.ignored_fields
    )
    auto_fields = tuple(field.name for field in raw.fields if field.name not in assigned)
    front_content = frozenset(_reconcile_key(section.text) for section in front.sections)
    return CardPresentation(
        raw.identity,
        front,
        _field_side(
            "back",
            profile.answer_fields + auto_fields,
            raw.fields,
            auto_names=frozenset(auto_fields),
            rendered_side=_render_document(raw.back_html, raw.back_av),
            excluded_auto_content=front_content,
            card_css=raw.card_css,
        ),
        profile if suggested else None,
    )


def default_field_profile(
    raw: RawCardContent,
    presentation: CardPresentation,
) -> TemplateFieldProfile | None:
    """Build a safe starting profile for a user-opened field editor."""
    available = {field.name for field in raw.fields}

    def source_fields(side: CardSide) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                section.source_label
                for section in side.sections
                if section.source_label in available
            )
        )

    populated = tuple(
        field.name for field in raw.fields if _render_document(field.html).text
    )
    prompt = source_fields(presentation.front)
    if not prompt and populated:
        prompt = (populated[0],)
    answer = tuple(
        name for name in source_fields(presentation.back) if name not in prompt
    )
    if not answer:
        answer = tuple(name for name in populated if name not in prompt)[:1]
    return TemplateFieldProfile(prompt, answer) if prompt and answer else None


def present_card(
    raw: RawCardContent,
    profile: TemplateFieldProfile | None = None,
) -> CardPresentation:
    """Convert one rendered Anki card to a complete immutable presentation."""
    return _with_hidden_names(_present_card(raw, profile), raw)


def card_media_names(raw: RawCardContent) -> frozenset[str]:
    """Every media name referenced by the card's rendered sides, fields and AV list."""
    return media_names(
        (raw.front_html, raw.back_html, *(field.html for field in raw.fields)),
        extra=(reference.label or "" for reference in raw.front_av + raw.back_av),
    )


def _with_hidden_names(presentation: CardPresentation, raw: RawCardContent) -> CardPresentation:
    names = card_media_names(raw)
    if not names:
        return presentation

    def side(card_side: CardSide) -> CardSide:
        return CardSide(tuple(
            replace(section, hidden_names=section.hidden_names | names)
            for section in card_side.sections
        ))

    return replace(presentation, front=side(presentation.front), back=side(presentation.back))


def _present_card(
    raw: RawCardContent,
    profile: TemplateFieldProfile | None = None,
) -> CardPresentation:
    if profile is not None:
        available = {field.name for field in raw.fields}
        selected = profile.prompt_fields + profile.answer_fields
        if profile.prompt_fields and profile.answer_fields and all(
            name in available for name in selected
        ):
            return _present_fields(raw, profile)
    front = _present_side("front", raw.front_html, raw.fields, raw.front_av)
    back_html, used_answer_marker = _strip_answer_html(raw.back_html)
    used_exact_front = False
    if not used_answer_marker and back_html.startswith(raw.front_html):
        back_html = back_html[len(raw.front_html) :]
        used_exact_front = True
    back = _present_side("back", back_html, raw.fields, raw.back_av)
    if not used_answer_marker and not used_exact_front:
        back = _strip_plain_front(back, front)
    presentation = CardPresentation(raw.identity, front, back)
    suggestion = _suggest_field_profile(raw, presentation)
    return _present_fields(raw, suggestion, suggested=True) if suggestion else presentation


def html_to_text(html: str, *, answer: bool = False) -> str:
    """Render standalone Anki HTML with readings inline for plain-text consumers."""
    source = _strip_answer_html(html)[0] if answer else html
    document = _render_document(source)
    text = document.text
    for _start, end, reading in sorted(set(document.furigana), reverse=True):
        text = f"{text[:end]}（{reading}）{text[end:]}"
    return text or "(empty card)"
