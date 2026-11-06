"""A safety net that does not depend on the HTML parser: every media file a card references is
scrubbed from all display text, whatever the parser made of the markup.

References come only from real markup and Anki's sound syntax:
- the attributes src, href, srcset (each URL), data and poster, read from inside tags (`<...>`),
  never from plain text and never from the content of `<pre>` or `<code>`;
- CSS `url(...)` in style attributes and `<style>` blocks;
- `[sound:...]`.

An attribute value loses its `?query` and `#fragment`. A URL with a scheme (`://`) or `//host`
contributes only its path's last segment, never its host. A reference counts only if its final
extension is a known media or file extension (MEDIA_EXTENSIONS, case-insensitive), for every
reference kind, so dotted words (e.g., 3.5, example.org, v2.0) are never collected. For each
reference, the value, its percent-decoded and once-more-percent-encoded forms and their basenames
are hidden. A bare word is never scrubbed: language decks name audio after the answer word
(hallo.mp3), and the card must still print that word. Matching treats any run of whitespace,
NBSP or underscore in a name as any such run in the text. Neutral labels such as `[audio 2]` are
never touched. Playback identities (ImageSpan.source, AV labels) are not display text and stay real.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import quote, unquote

from rich.text import Text

from .card_text import substitute

SCRUBBED = "[media]"

MEDIA_EXTENSIONS = frozenset({
    # audio
    "mp3", "wav", "ogg", "oga", "opus", "m4a", "aac", "flac", "wma", "aif", "aiff", "aifc",
    "amr", "spx", "mid", "midi", "weba", "caf",
    # video
    "mp4", "m4v", "webm", "mkv", "mov", "avi", "ogv", "wmv", "mpg", "mpeg", "flv", "3gp", "3g2",
    # image
    "png", "jpg", "jpeg", "jpe", "gif", "webp", "bmp", "tif", "tiff", "ico", "avif", "heic",
    "heif", "jxl", "apng",
    # svg, documents, subtitles, flash
    "svg", "svgz", "pdf", "vtt", "srt", "swf",
})

_MEDIA_ATTRIBUTES = frozenset({"src", "href", "srcset", "data", "poster"})
_TAG = re.compile(r"""<[A-Za-z][^\s/>]*((?:[^>"']|"[^"]*"|'[^']*')*)>""")
_ATTRIBUTE = re.compile(r"""([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
_LITERAL = re.compile(r"<(pre|code)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)""", re.IGNORECASE)
_SOUND = re.compile(r"\[sound:([^\]]+)\]", re.IGNORECASE)
_GAP = r"[\s_]+"  # \s includes NBSP for str patterns
# Display labels that must survive the scrub unchanged.
_KEEP = r"\[(?:audio|image|video|math|media)(?: [0-9]+)?\]"
_SOURCE_SAFE = "/:._-~()+,;=&'!$*@"


def _css_urls(css: str) -> Iterable[str]:
    for match in _CSS_URL.finditer(css):
        yield next((group for group in match.groups() if group is not None), "")


def _references(source: str) -> Iterable[tuple[str, str]]:
    """(kind, value) for every media reference in raw HTML or text."""
    for match in _SOUND.finditer(source):
        yield "sound", match.group(1)
    markup = _LITERAL.sub(" ", source)  # code samples are text, not references
    for block in _STYLE_BLOCK.finditer(markup):
        for url in _css_urls(block.group(1)):
            yield "url", url
    for tag in _TAG.finditer(markup):
        for attribute in _ATTRIBUTE.finditer(tag.group(1)):
            name = attribute.group(1).lower()
            raw = next((group for group in attribute.groups()[1:] if group is not None), None)
            if raw is None:
                continue
            value = html.unescape(raw)
            if name == "style":
                for url in _css_urls(value):
                    yield "url", url
            elif name == "srcset":
                for candidate in value.split(","):
                    if candidate.split():
                        yield "srcset", candidate.split()[0]
            elif name in _MEDIA_ATTRIBUTES:
                yield name, value


def _file_part(kind: str, value: str) -> str:
    """The part of a reference that names a file ("" if none)."""
    value = value.strip()
    if kind == "sound":
        return value                            # Anki file names are used as they are
    if value.lower().startswith(("data:", "javascript:", "mailto:", "about:")):
        return ""
    value = value.split("#", 1)[0].split("?", 1)[0]
    if "://" in value or value.startswith("//"):
        rest = value.split("//", 1)[1]
        path = rest.split("/", 1)[1] if "/" in rest else ""
        return path.rstrip("/").rsplit("/", 1)[-1] if path.strip("/") else ""
    return value


def has_media_extension(name: str) -> bool:
    stem, dot, extension = name.strip().rpartition(".")
    return bool(dot and stem.strip() and extension.lower() in MEDIA_EXTENSIONS)


def media_names(sources: Iterable[str], extra: Iterable[str] = ()) -> frozenset[str]:
    """Names to hide for a card: from its raw HTML/text `sources` and raw `extra` file names."""
    names: set[str] = set()
    references = [reference for source in sources for reference in _references(source)]
    references += [("sound", value) for value in extra if value]
    for kind, value in references:
        part = _file_part(kind, value)
        if not part:
            continue
        decoded = unquote(part)
        forms = {part, decoded, quote(part, safe=_SOURCE_SAFE), quote(decoded, safe=_SOURCE_SAFE)}
        for form in forms:
            base = form.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            names.update(name.strip() for name in (form, base) if has_media_extension(name))
    return frozenset(names)


def _tolerant(name: str) -> str:
    return _GAP.join(re.escape(part) for part in re.split(_GAP, name))


@lru_cache(maxsize=256)
def _pattern(names: frozenset[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    ordered = sorted(names, key=lambda name: (-len(name), name))
    alternatives = "|".join(_tolerant(name) for name in ordered)
    return re.compile(rf"(?P<keep>{_KEEP})|(?:{alternatives})")


def _replacement(match: re.Match[str]) -> str:
    return match.group() if match.group("keep") else SCRUBBED


def scrub_text(text: Text, names: frozenset[str]) -> Text:
    """Replace every hidden name in display `text` with [media], keeping its marks."""
    pattern = _pattern(names)
    if pattern is None or not pattern.search(text.plain):
        return text
    return substitute(text, pattern, _replacement)


def scrub(value: str, names: frozenset[str]) -> str:
    pattern = _pattern(names)
    return value if pattern is None else pattern.sub(_replacement, value)
