"""Find where a write, a brief, or a command sets a motion's length or easing.

The skill gate asks for the motion skills before such a call. The caller says
what kind of text it is; this module finds the pieces of motion in it (a
declaration, a call, a block, a token line, or a sentence) and compares two
versions: a write counts when the motion it leaves differs from the motion it
found, or when the range an edit changes falls inside a piece of motion, so a
changed duration counts and motion left as it was does not. It is a text
heuristic, not a parser: it reads nothing a command would compute, and some
sentences about switching or timing that are not visual motion count too.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# A time starts only where no digit or point stands before it, so a long run of digits is tried once, not at each digit.
_CODE_TIME = r"(?<![\d.])(?:\d+(?:\.\d+)?|\.\d+)m?s\b"
# English names are bounded by ASCII characters only, so a Korean particle right after one ("ease-out으로") ends it.
_SHARED_CODE = (
    r"@keyframes\b|cubic-bezier\(|steps\(\d|::view-transition|startViewTransition\s*\(|\.animate\s*\("
    r"|(?<![A-Za-z0-9_.$])animate\s*\(\s*[\w$.\[\]'\"]+\s*,"
    r"|\bgsap\.(?:to|from|fromTo|set|timeline)\s*\(|\banime\s*\(\s*\{"
    r"|<motion\.[A-Za-z]+|\btransition=\{\{|\banimate=\{\{|<animate(?:Transform|Motion)?\b|\bdur=[\"']"
    r"|(?<![A-Za-z0-9_-])(?:duration|delay)-(?:\d{2,4}|\[)"
    r"|(?<![A-Za-z0-9_-])ease-(?:in-out|in|out|linear)(?![A-Za-z0-9_-])"
    r"|(?<![A-Za-z0-9_-])animate-(?:spin|ping|pulse|bounce|none|\[)"
    r"|(?:transition|animation)(?:Duration|Delay|TimingFunction)[\"']?\s*[:=]\s*['\"`\d]"
    r"|(?<![A-Za-z0-9_-])transition\s*:\s*\{\s*(?:duration|delay|ease|type|stiffness)"
    r"|(?<![A-Za-z0-9_-])(?:transition|animation)(?:-[a-z-]+)?\s*:\s*[^;\n{}]*?"
    rf"(?:{_CODE_TIME}|ease|cubic-bezier|linear|steps)"
    rf"|(?<![A-Za-z0-9_-])(?:transition|animation)\s*=\s*['\"`][^'\"`\n]*?(?:{_CODE_TIME}|ease)"
)
# A style sheet's `transition:` is always motion; in a script it is as often a type annotation or a state field.
_STYLE_PROPERTY = r"(?<![A-Za-z0-9_-])(?:transition|animation)(?:-[a-z-]+)?\s*:"
_MOTION_WORDS = (
    r"전환|애니메이션|움직임|움직이|(?<!프로)모션|트랜지션|이징|페이드|등장|나타나|사라지|슬라이드"
    r"|(?<![A-Za-z])(?:transitions?|animations?|animate[ds]?|motion|easing|ease-(?:in-out|in|out)"
    r"|fade(?:-in|-out)?|keyframes|slide(?:s|-in|-out)?)(?![A-Za-z])"
)
# 초 right after a number is seconds; after a space it may start a word
# (초안 a draft, 초과 excess, 초대 an invitation).
_TIME_VALUE = (
    r"(?<![A-Za-z0-9_.])(?:\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s?(?:ms|밀리초|milliseconds?|seconds?|secs?)(?![A-Za-z])|s(?![A-Za-z])|초|\s초(?!안(?![에쪽으])|과|기|대|점|록))"
)
_EASING_VALUE = (
    r"(?<![A-Za-z0-9_-])"
    r"(?:ease-(?:in-out|in|out)|ease|linear|cubic-bezier|spring|ease[A-Z][A-Za-z]*|power\d(?:\.[A-Za-z]+)?)"
    r"(?![A-Za-z0-9_-])"
)
_DURATION_KEY = r"(?<![A-Za-z0-9_-])[\"']?(?:duration|delay|easing)[\"']?\s*[:=]\s*[\"']?[\w.]"
# An in-place edit names an easing only by a function or keyword, since `linear` there is as often a gradient.
_IN_PLACE_DETAIL = rf"{_TIME_VALUE}|(?<![A-Za-z0-9_-])ease-(?:in-out|in|out)(?![A-Za-z0-9_-])|cubic-bezier|steps\("
_SENTENCE_BREAK = r"(?<=[.!?])\s+|\n"
_LINE = r"[^\n]+"
_SPACES = re.compile(r"\s+")
_SPACE = " "
_MOTION_WINDOW = 80
_UNIT_LIMIT = 4000
_BRACKET_DEPTHS = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}
_BLOCK_CLOSE = "}"
_QUOTES = frozenset("\"'`")
_UNIT_STOPS = frozenset(";\"'`>")
_ESCAPE = "\\"
_LINE_END = "\n"
_CONTINUATIONS = (":", "：", ",")
_LINE_PADDING = " \t"


class TextKind(Enum):
    """How the caller's text is read: by its file kind, or as prose for briefs and prompts."""

    STYLE = "style"
    SCRIPT = "script"
    TOKENS = "tokens"
    PROSE = "prose"


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Patterns:
    """The compiled expressions, built on first use: the hook runs on every tool call, and most never read motion."""

    script_motion: re.Pattern[str]
    style_motion: re.Pattern[str]
    motion_words: re.Pattern[str]
    motion_detail: re.Pattern[str]
    in_place_detail: re.Pattern[str]
    sentence_break: re.Pattern[str]
    line: re.Pattern[str]


@functools.cache
def _get_patterns() -> _Patterns:
    return _Patterns(
        script_motion=re.compile(_SHARED_CODE),
        style_motion=re.compile(f"{_SHARED_CODE}|{_STYLE_PROPERTY}"),
        motion_words=re.compile(_MOTION_WORDS, re.IGNORECASE),
        motion_detail=re.compile(f"{_TIME_VALUE}|{_EASING_VALUE}|{_DURATION_KEY}"),
        in_place_detail=re.compile(_IN_PLACE_DETAIL),
        sentence_break=re.compile(_SENTENCE_BREAK),
        line=re.compile(_LINE),
    )


def is_motion_change(kind: TextKind, before: str, after: str, changed: Sequence[tuple[int, int]] = ()) -> bool:
    """Whether `after` holds motion `before` did not, or a changed range of `after` falls inside a piece of motion."""
    after_units = _list_units(kind, after)
    added = Counter(unit.text for unit in after_units) - Counter(unit.text for unit in _list_units(kind, before))
    is_touched = any(start < unit.end and unit.start < end for unit in after_units for start, end in changed)

    return bool(added) or is_touched


def has_motion_text(text: str) -> bool:
    """Read as prose, since a brief or a prompt may quote code and describe motion in words alike."""
    return bool(_list_units(TextKind.PROSE, text))


def has_in_place_motion(kind: TextKind, expression: str, existing: str) -> bool:
    """Whether an in-place edit writes motion, or changes a time or easing in a style sheet or a motion file."""
    has_detail = bool(_get_patterns().in_place_detail.search(expression))
    is_motion_file = kind is TextKind.STYLE or bool(_list_units(kind, existing))

    return bool(_list_units(kind, expression)) or (has_detail and is_motion_file)


def _list_units(kind: TextKind, text: str) -> list[_Unit]:
    patterns = _get_patterns()

    if kind is TextKind.STYLE:
        return _list_code_units(patterns.style_motion, text, False)
    elif kind is TextKind.SCRIPT:
        return _list_code_units(patterns.script_motion, text, True)
    elif kind is TextKind.TOKENS:
        return [*_list_code_units(patterns.script_motion, text, True), *_list_token_units(text)]
    else:
        return [*_list_code_units(patterns.script_motion, text, True), *_list_sentence_units(text)]


def _list_code_units(pattern: re.Pattern[str], text: str, stops_at_line_end: bool) -> list[_Unit]:
    return [_read_code_unit(text, match, stops_at_line_end) for match in pattern.finditer(text)]


def _read_code_unit(text: str, match: re.Match[str], stops_at_line_end: bool) -> _Unit:
    """The motion code a match starts: the rest of its declaration, or the call or block it opens until that closes.

    A style declaration goes on over line breaks to its `;`; quoted text
    inside open brackets is skipped, so brackets in a selector string do not count.
    """
    depth = sum(_BRACKET_DEPTHS.get(character, 0) for character in match.group())
    opens_brackets = depth > 0
    index = match.end()
    limit = min(len(text), match.end() + _UNIT_LIMIT)

    while index < limit and not (depth <= 0 and _is_unit_end(text, index, stops_at_line_end)):
        character = text[index]
        depth += _BRACKET_DEPTHS.get(character, 0)
        index = _skip_quoted(text, index, limit) if depth > 0 and character in _QUOTES else index + 1

        if depth == 0 and _BRACKET_DEPTHS.get(character, 0) < 0 and (opens_brackets or character == _BLOCK_CLOSE):
            break
    return _Unit(match.start(), index, _normalize_spaces(text[match.start() : index]))


def _is_unit_end(text: str, index: int, stops_at_line_end: bool) -> bool:
    """Outside brackets a unit ends at a separator, a quote, an unmatched closer, or a line that does not go on."""
    character = text[index]
    is_line_end = stops_at_line_end and character == _LINE_END and not _follows_continuation(text, index)

    return character in _UNIT_STOPS or _BRACKET_DEPTHS.get(character, 0) < 0 or is_line_end


def _skip_quoted(text: str, index: int, limit: int) -> int:
    """The index just past the quoted text that starts at the index, or the limit when it does not close."""
    quote = text[index]
    position = index + 1

    while position < limit and text[position] != quote:
        position += 2 if text[position] == _ESCAPE else 1
    return min(position + 1, limit)


def _follows_continuation(text: str, index: int) -> bool:
    line = text[max(0, index - _UNIT_LIMIT) : index].rstrip(_LINE_PADDING)

    return line.endswith(_CONTINUATIONS)


def _list_token_units(text: str) -> list[_Unit]:
    """In a token file that names motion anywhere, each line with a duration, delay, easing, or time."""
    patterns = _get_patterns()
    lines = [match for match in patterns.line.finditer(text) if patterns.motion_detail.search(match.group())]
    names_motion = bool(patterns.motion_words.search(text))

    return [_Unit(line.start(), line.end(), line.group().strip()) for line in lines] if names_motion else []


def _list_sentence_units(text: str) -> list[_Unit]:
    units = [_Unit(start, end, _normalize_spaces(text[start:end])) for start, end in _split_sentences(text)]

    return [unit for unit in units if _has_motion_detail(unit.text)]


def _normalize_spaces(text: str) -> str:
    return _SPACES.sub(_SPACE, text).strip()


def _split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence and line ranges; a line ending in a colon or a comma goes on, as in "전환 길이:" then "0.8초"."""
    spans: list[tuple[int, int]] = []
    start = 0

    for match in _get_patterns().sentence_break.finditer(text):
        is_continued = match.group() == _LINE_END and _follows_continuation(text, match.start())

        if not is_continued:
            spans.append((start, match.start()))
            start = match.end()
    return [*spans, (start, len(text))]


def _has_motion_detail(sentence: str) -> bool:
    """Whether a motion word stands near a time, an easing, or a duration key in the sentence."""
    patterns = _get_patterns()

    return any(
        patterns.motion_detail.search(sentence, max(0, word.start() - _MOTION_WINDOW), word.end() + _MOTION_WINDOW)
        for word in patterns.motion_words.finditer(sentence)
    )
