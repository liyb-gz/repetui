"""A safety net that does not depend on the HTML parser: every media name a card references in
its raw HTML or text is scrubbed from all display text, whatever the parser made of the markup.

Names come from the attributes src, href, srcset (each URL), data and poster, from CSS url(...),
and from [sound:...]. For each reference, the full value, the percent-decoded value and the
basename are hidden, but only names that contain ".", "/" or "%". A bare word (a file's stem, or a
name without an extension) is never scrubbed: language decks often name audio after the answer
word (hallo.mp3), and that word must still show where the card itself prints it. Neutral labels
such as `[audio 2]` are never touched. Playback identities (ImageSpan.source, AV labels) are not
display text and stay real.
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

_ATTRIBUTE = re.compile(
    r"""(?<![\w])(src|href|srcset|data|poster)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""",
    re.IGNORECASE,
)
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)""", re.IGNORECASE)
_SOUND = re.compile(r"\[sound:([^\]]+)\]", re.IGNORECASE)
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")
# Display labels that must survive the scrub unchanged.
_KEEP = r"\[(?:audio|image|video|math|media)(?: [0-9]+)?\]"
_SOURCE_SAFE = "/:._-~()+,;=&'!$*@"


def _references(source: str) -> Iterable[tuple[str, str]]:
    """(kind, value) for every media reference in raw HTML or text."""
    for text in {source, html.unescape(source)}:
        for match in _ATTRIBUTE.finditer(text):
            name = match.group(1).lower()
            value = next(group for group in match.groups()[1:] if group is not None)
            if name == "srcset":
                for candidate in value.split(","):
                    if candidate.split():
                        yield "srcset", candidate.split()[0]
            else:
                yield name, value
        for match in _CSS_URL.finditer(text):
            yield "url", next((group for group in match.groups() if group is not None), "")
        for match in _SOUND.finditer(text):
            yield "sound", match.group(1)


def media_names(sources: Iterable[str], extra: Iterable[str] = ()) -> frozenset[str]:
    """Names to hide for a card: from its raw HTML/text `sources` and raw `extra` file names."""
    names: set[str] = set()
    references = [reference for source in sources for reference in _references(source)]
    references += [("file", value) for value in extra if value]
    for kind, value in references:
        value = value.strip()
        if not value or value.lower().startswith(("data:", "javascript:", "#", "about:")):
            continue
        for form in {value, unquote(value), quote(unquote(value), safe=_SOURCE_SAFE)}:
            # never urlsplit: a malformed URL ("http://[broken") must not stop a card rendering
            path = form.split("#", 1)[0].split("?", 1)[0] if "://" in form else form
            base = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            if kind == "href" and not _EXTENSION.search(base):
                continue  # a link to a page, not a media file: never scrub its words
            names.update((form, base))
    return frozenset(name for name in names if _hideable(name))


def _hideable(name: str) -> bool:
    """Only file-like names: a bare word is card content, not a leaked file name."""
    return bool(name.strip()) and any(mark in name for mark in "./%")


@lru_cache(maxsize=256)
def _pattern(names: frozenset[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    ordered = sorted(names, key=lambda name: (-len(name), name))
    alternatives = "|".join(re.escape(name) for name in ordered)
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
