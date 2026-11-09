"""Keep only the line changes a session made itself, judged from its write record.

For each write the session made to a file, the record holds the content just
before and just after it. Whatever changed between two of those writes, or
after the last one, came from elsewhere: another session, another tool, or a
person. Lines are followed through that chain the way blame does, instead of
being replayed with a three-way merge, because a three-way merge conflicts
whenever another session appends right next to this session's append, the
usual case in a shared log.
"""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

_EQUAL_TAG = "equal"
_DELETE_TAGS = frozenset({"delete", "replace"})
_INSERT_TAGS = frozenset({"insert", "replace"})


class LineOrigin(str, Enum):
    EARLIER = "earlier"
    OWN = "own"
    OTHER = "other"


class ChangeKind(str, Enum):
    ADDED = "+"
    REMOVED = "-"


@dataclass(frozen=True)
class OwnWrite:
    before: str
    after: str
    others_inserted: Counter[str]


@dataclass(frozen=True)
class FileHistory:
    first_before: str
    own_writes: tuple[OwnWrite, ...]
    working_tree: str


@dataclass(frozen=True)
class OwnContent:
    text: str
    entangled: tuple[str, ...]


@dataclass(frozen=True)
class ForeignLine:
    kind: ChangeKind
    line_number: int
    text: str


@dataclass
class _LineRecord:
    text: str
    origin: LineOrigin
    deleted_by: LineOrigin | None = None
    replaced: tuple[_LineRecord, ...] = ()
    step: int = 0
    position: int = 0


_Opcode = tuple[str, int, int, int, int]


def build_own_content(base: str, history: FileHistory) -> OwnContent:
    """Return base plus this session's changes that are still in the working tree.

    Own lines that replace, or sit inside, lines another session has not
    committed cannot be committed apart from them; they come back in
    `entangled` so the caller can stop and ask instead.
    """
    base_lines = base.splitlines(keepends=True)
    weave = _build_weave(history)
    others = [
        index for index, record in enumerate(weave) if record.deleted_by is None and record.origin != LineOrigin.OWN
    ]
    lines: list[str] = []
    entangled: list[str] = []
    cursor = 0

    for tag, i1, i2, j1, j2 in _diff_opcodes(base_lines, [weave[index].text for index in others]):
        if tag == _EQUAL_TAG:
            for offset in range(i2 - i1):
                anchor = others[j1 + offset]
                _add_region(lines, entangled, weave[cursor:anchor], [])
                lines.append(base_lines[i1 + offset])
                cursor = anchor + 1
        else:
            region_end = others[j2] if j2 < len(others) else len(weave)
            _add_region(lines, entangled, weave[cursor:region_end], base_lines[i1:i2])
            cursor = region_end
    _add_region(lines, entangled, weave[cursor:], [])
    return OwnContent("".join(lines), tuple(entangled))


def list_foreign_lines(base: str, staged: str, own: str) -> list[ForeignLine]:
    """List staged changes that the own content does not have, numbered as in base and staged."""
    own_added, own_removed = _count_changes(base, own)
    base_lines = base.splitlines(keepends=True)
    staged_lines = staged.splitlines(keepends=True)
    foreign: list[ForeignLine] = []

    for tag, i1, i2, j1, j2 in _diff_opcodes(base_lines, staged_lines):
        if tag != _EQUAL_TAG:
            foreign.extend(_take_foreign(ChangeKind.REMOVED, base_lines, i1, i2, own_removed))
            foreign.extend(_take_foreign(ChangeKind.ADDED, staged_lines, j1, j2, own_added))
    return foreign


def _build_weave(history: FileHistory) -> list[_LineRecord]:
    first_lines = history.first_before.splitlines(keepends=True)
    weave = [_LineRecord(line, LineOrigin.EARLIER, position=index) for index, line in enumerate(first_lines)]
    current = history.first_before

    for step, write in enumerate(history.own_writes, start=1):
        if write.before != current:
            weave, _ = _apply_version(weave, write.before, LineOrigin.OTHER, -step)
        weave, inserted = _apply_version(weave, write.after, LineOrigin.OWN, step)
        _mark_others_inserted(inserted, write.others_inserted)
        current = write.after
    if history.working_tree != current:
        weave, _ = _apply_version(weave, history.working_tree, LineOrigin.OTHER, len(history.own_writes) + 1)
    return weave


def _apply_version(
    weave: list[_LineRecord],
    text: str,
    owner: LineOrigin,
    step: int,
) -> tuple[list[_LineRecord], list[_LineRecord]]:
    live = [record for record in weave if record.deleted_by is None]
    new_lines = text.splitlines(keepends=True)
    inserts: dict[int, list[_LineRecord]] = {}

    for tag, i1, i2, j1, j2 in _diff_opcodes([record.text for record in live], new_lines):
        replaced = tuple(live[i1:i2]) if owner == LineOrigin.OWN and tag in _DELETE_TAGS else ()

        if tag in _DELETE_TAGS:
            for record in live[i1:i2]:
                record.deleted_by = owner
        if tag in _INSERT_TAGS:
            new_records = (_LineRecord(new_lines[j], owner, None, replaced, step, j) for j in range(j1, j2))
            inserts.setdefault(i2, []).extend(new_records)
    return _weave_inserts(weave, live, inserts), [record for group in inserts.values() for record in group]


def _weave_inserts(
    weave: list[_LineRecord],
    live: list[_LineRecord],
    inserts: dict[int, list[_LineRecord]],
) -> list[_LineRecord]:
    live_positions = {id(record): index for index, record in enumerate(live)}
    woven: list[_LineRecord] = []

    for record in weave:
        position = live_positions.get(id(record))

        if position is not None:
            woven.extend(inserts.get(position, []))
        woven.append(record)
    woven.extend(inserts.get(len(live), []))
    return woven


def _mark_others_inserted(inserted: list[_LineRecord], others_inserted: Counter[str]) -> None:
    """Hand back lines that another session wrote into the file while this session's command ran."""
    remaining = Counter(others_inserted)

    for record in inserted:
        if remaining[record.text] > 0:
            record.origin = LineOrigin.OTHER
            remaining[record.text] -= 1


def _add_region(lines: list[str], entangled: list[str], region: list[_LineRecord], base_gap: list[str]) -> None:
    region_lines, region_entangled = _render_region(region, base_gap)

    lines.extend(region_lines)
    entangled.extend(region_entangled)


def _render_region(region: list[_LineRecord], base_gap: list[str]) -> tuple[list[str], list[str]]:
    """Own live lines of one stretch, plus the base lines there that this session did not remove.

    A base line matched to a removed line counts as this session's removal
    whoever wrote that line, because a line this session wrote and committed,
    or put back by restoring the file, is in the base too. Lines of others
    that are still live in a stretch are uncommitted (committed ones are the
    base lines that bound it), which is how entangled own lines are found.
    """
    own_live = [record for record in region if record.origin == LineOrigin.OWN and record.deleted_by is None]
    committed_own = {i for i, _ in _match_texts(base_gap, [record.text for record in own_live])}
    remaining = [index for index in range(len(base_gap)) if index not in committed_own]
    tombstones = [record for record in region if record.deleted_by is not None]
    pairs = _match_texts([base_gap[index] for index in remaining], [record.text for record in tombstones])
    matched = {remaining[i] for i, _ in pairs}
    restored = {
        id(tombstones[j]): base_gap[remaining[i]] for i, j in pairs if tombstones[j].deleted_by != LineOrigin.OWN
    }
    committed = {id(tombstones[j]) for _, j in pairs}
    lines = [base_gap[index] for index in remaining if index not in matched]

    for record in region:
        if record.origin == LineOrigin.OWN and record.deleted_by is None:
            lines.append(record.text)
        elif id(record) in restored:
            lines.append(restored[id(record)])
    return lines, _find_entangled(region, committed)


def _find_entangled(region: list[_LineRecord], committed: set[int]) -> list[str]:
    """Own lines that replace another session's uncommitted line, or split lines it wrote in one go."""
    entangled: list[str] = []

    for index, record in enumerate(region):
        is_own_live = record.origin == LineOrigin.OWN and record.deleted_by is None
        replaces_uncommitted = any(
            replaced.origin != LineOrigin.OWN and id(replaced) not in committed for replaced in record.replaced
        )

        if is_own_live and (replaces_uncommitted or _is_splitting(region, index)):
            entangled.append(record.text)
    return entangled


def _is_splitting(region: list[_LineRecord], index: int) -> bool:
    before = _find_other_live(reversed(region[:index]))
    after = _find_other_live(region[index + 1 :])

    return (
        before is not None and after is not None and before.step == after.step and after.position == before.position + 1
    )


def _find_other_live(records: Iterable[_LineRecord]) -> _LineRecord | None:
    return next((record for record in records if record.deleted_by is None and record.origin != LineOrigin.OWN), None)


def _match_texts(left: list[str], right: list[str]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []

    for tag, i1, i2, j1, _ in _diff_opcodes(left, right):
        if tag == _EQUAL_TAG:
            pairs.extend((i1 + offset, j1 + offset) for offset in range(i2 - i1))
    return pairs


def _count_changes(old: str, new: str) -> tuple[Counter[str], Counter[str]]:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    added: Counter[str] = Counter()
    removed: Counter[str] = Counter()

    for tag, i1, i2, j1, j2 in _diff_opcodes(old_lines, new_lines):
        if tag != _EQUAL_TAG:
            removed.update(old_lines[i1:i2])
            added.update(new_lines[j1:j2])
    return added, removed


def _take_foreign(kind: ChangeKind, lines: list[str], start: int, end: int, own: Counter[str]) -> list[ForeignLine]:
    foreign: list[ForeignLine] = []

    for index in range(start, end):
        if own[lines[index]] > 0:
            own[lines[index]] -= 1
        else:
            foreign.append(ForeignLine(kind, index + 1, lines[index].rstrip("\n")))
    return foreign


def _diff_opcodes(old: list[str], new: list[str]) -> list[_Opcode]:
    """SequenceMatcher opcodes, matching only what lies between the common prefix and suffix."""
    limit = min(len(old), len(new))
    prefix = next((index for index in range(limit) if old[index] != new[index]), limit)
    suffix = next((index for index in range(limit - prefix) if old[-1 - index] != new[-1 - index]), limit - prefix)
    matcher = difflib.SequenceMatcher(
        None, old[prefix : len(old) - suffix], new[prefix : len(new) - suffix], autojunk=False
    )
    middle = [
        (tag, i1 + prefix, i2 + prefix, j1 + prefix, j2 + prefix) for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]
    head: list[_Opcode] = [(_EQUAL_TAG, 0, prefix, 0, prefix)] if prefix else []
    tail: list[_Opcode] = [(_EQUAL_TAG, len(old) - suffix, len(old), len(new) - suffix, len(new))] if suffix else []

    return head + middle + tail
