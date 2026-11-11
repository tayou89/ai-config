"""Keep only the line changes a session made itself, judged from its write record.

For each write the session made to a file, the record holds the content just
before and just after it. Whatever changed between two of those writes, or
after the last one, came from elsewhere: another session, another tool, or a
person. Lines are followed through that chain the way blame does, instead of
being replayed with a three-way merge, because a three-way merge conflicts
whenever another session appends right next to this session's append, the
usual case in a shared log.

A write whose snapshots may also hold another session's change is
contested: none of its changes is counted as this session's, and those the
commit would leave out are returned so the caller asks instead of guessing.
The record types are named apart from the earlier format's (OwnWrite,
OwnContent), so a process that loads an older copy of a sibling module
fails on import instead of reading the new records with the old meaning.
"""

from __future__ import annotations

import bisect
import difflib
from collections import Counter
from collections.abc import Callable
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
class RecordedWrite:
    before: str
    after: str
    contested: bool


@dataclass(frozen=True)
class FileHistory:
    first_before: str
    own_writes: tuple[RecordedWrite, ...]
    working_tree: str


@dataclass(frozen=True)
class ContestedLine:
    """A change that may be this session's, left out of the commit: an added line, or a removal kept undone."""

    kind: ChangeKind
    text: str


@dataclass(frozen=True)
class OwnCommitContent:
    text: str
    entangled: tuple[str, ...]
    contested: tuple[ContestedLine, ...]


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
    replaced_by: tuple[_LineRecord, ...] = ()
    step: int = 0
    position: int = 0
    contested: bool = False


_Opcode = tuple[str, int, int, int, int]


def build_own_content(base: str, history: FileHistory) -> OwnCommitContent:
    """Return base plus this session's changes that are still in the working tree.

    Each base line is tied to the weave record it is, so a removal is taken
    from the line this session removed even where other lines have the same
    text. Own lines that replace, or sit inside, lines another session has
    not committed cannot be committed apart from them, and neither can an own
    removal whose replacement another session has since changed; they come
    back in `entangled`. An own removal of a base line that another session
    has since written again in its place comes back in `contested`, since
    that line may be the removed one put back. When base has the text of
    several recorded versions, which of them base is cannot be told: if
    their records give different results, the lines they disagree on come
    back in `contested`, and if not, tangled lines and hidden removals are
    looked for in each of them. The result is then checked against base and
    the working tree: a change
    between base and it that no own write made, or a change it leaves out
    that a contested write made or no write explains, comes back in
    `contested` too. Either way the caller stops and asks.
    """
    base_lines = base.splitlines(keepends=True)
    first_lines = history.first_before.splitlines(keepends=True)
    working_lines = history.working_tree.splitlines(keepends=True)
    weave, versions = _build_weave(history)
    readings = [_link_base_lines(base_lines, weave, version) for version in _find_base_versions(base_lines, versions)]
    renders = [_render_lines(base_lines, weave, links) for links in readings]
    lines = renders[0]
    ambiguous = next((_list_line_changes(lines, other) for other in renders[1:] if other != lines), [])
    entangled = next(
        filter(None, (_find_entangled(weave, {id(weave[index]) for index in links.values()}) for links in readings)),
        [],
    )
    hidden = next(filter(None, (_find_hidden_removals(weave, set(links.values())) for links in readings)), [])
    unproven = _find_unproven_changes(base_lines, first_lines, lines, working_lines, weave)

    return OwnCommitContent("".join(lines), tuple(entangled), (*ambiguous, *hidden, *unproven))


def _find_base_versions(base: list[str], versions: list[list[_LineRecord]]) -> list[list[_LineRecord]]:
    """The recorded versions with the text of base, oldest first, or the version before the first write if none."""
    matching = [version for version in versions if [record.text for record in version] == base]

    return matching or versions[:1]


def _find_hidden_removals(weave: list[_LineRecord], linked: set[int]) -> list[ContestedLine]:
    """Own removals of base lines with a live line of others of the same text between the same kept base lines."""
    kept = [index for index in sorted(linked) if weave[index].deleted_by != LineOrigin.OWN]
    bounds = [-1, *kept, len(weave)]
    hidden: list[ContestedLine] = []

    for start, end in zip(bounds, bounds[1:]):
        stretch = weave[start + 1 : end]
        written_again = Counter(record.text for record in stretch if _is_live_other(record))

        hidden.extend(
            ContestedLine(ChangeKind.REMOVED, record.text)
            for index, record in enumerate(stretch, start=start + 1)
            if index in linked and record.deleted_by == LineOrigin.OWN and _take_count(written_again, record.text)
        )
    return hidden


def _find_unproven_changes(
    base: list[str],
    first_before: list[str],
    candidate: list[str],
    working_tree: list[str],
    weave: list[_LineRecord],
) -> list[ContestedLine]:
    """Changes the candidate makes that no own write made, and changes it leaves out that are not surely others'.

    Line attribution can go wrong where lines share text, so its result is
    checked by what it contains rather than trusted. Base → candidate must be
    own changes, counted by text, because a diff may tell this session's
    move of a line as a move of another line with the same text. Candidate →
    working tree must be others' changes that are not contested, in order,
    so a line put in the wrong place is caught. What differs between base
    and the content before this session's first write is others' work from
    before it: lines from then that are still there but not in base are
    others' additions, and base lines gone by then are others' removals.
    Both are counted by text, never by a diff that could pair the wrong
    lines, and a removal does not count for the text of a line anyone wrote
    since, which may have been committed since and removed by anyone.
    """
    written_since = Counter(record.text for record in weave if record.origin != LineOrigin.EARLIER)
    still_there = Counter(
        record.text for record in weave if record.origin == LineOrigin.EARLIER and record.deleted_by is None
    )
    added_earlier = still_there - Counter(base)
    removed_earlier = Counter(base) - Counter(first_before) - written_since
    own_added = Counter(
        record.text for record in weave if record.origin == LineOrigin.OWN and record.deleted_by is None
    )
    own_removed = Counter(record.text for record in weave if record.deleted_by == LineOrigin.OWN)
    contested_added = Counter(record.text for record in weave if record.contested and record.deleted_by is None)
    contested_removed = Counter(
        record.text for record in weave if record.contested and record.deleted_by == LineOrigin.OTHER
    )
    foreign_added = (
        Counter(
            record.text
            for record in weave
            if record.origin == LineOrigin.OTHER and record.deleted_by is None and not record.contested
        )
        + added_earlier
    )
    foreign_removed = (
        Counter(record.text for record in weave if record.deleted_by == LineOrigin.OTHER and not record.contested)
        + removed_earlier
    )
    unexplained_removed = Counter(base) - Counter(candidate) - own_removed
    unexplained_added = Counter(candidate) - Counter(base) - own_added
    left_out = _list_line_changes(candidate, working_tree)

    return [
        *(ContestedLine(ChangeKind.REMOVED, line) for line in base if _take_count(unexplained_removed, line)),
        *(ContestedLine(ChangeKind.ADDED, line) for line in candidate if _take_count(unexplained_added, line)),
        *(
            line
            for line in left_out
            if _take_count(contested_removed if line.kind == ChangeKind.REMOVED else contested_added, line.text)
            or not _take_count(foreign_removed if line.kind == ChangeKind.REMOVED else foreign_added, line.text)
        ),
    ]


def _list_line_changes(old: list[str], new: list[str]) -> list[ContestedLine]:
    """The lines a diff from old to new removes and adds, in order, removals first within each change."""
    changes: list[ContestedLine] = []

    for tag, i1, i2, j1, j2 in _diff_opcodes(old, new):
        if tag != _EQUAL_TAG:
            changes.extend(ContestedLine(ChangeKind.REMOVED, line) for line in old[i1:i2])
            changes.extend(ContestedLine(ChangeKind.ADDED, line) for line in new[j1:j2])
    return changes


def _take_count(counts: Counter[str], text: str) -> bool:
    """Use up one occurrence of text from counts, saying whether there was one."""
    if counts[text] > 0:
        counts[text] -= 1
        return True
    else:
        return False


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


def _build_weave(history: FileHistory) -> tuple[list[_LineRecord], list[list[_LineRecord]]]:
    """Every line the history shows, in file order, and the records each recorded version of the file holds."""
    first_lines = history.first_before.splitlines(keepends=True)
    weave = [_LineRecord(line, LineOrigin.EARLIER, position=index) for index, line in enumerate(first_lines)]
    versions = [list(weave)]
    current = history.first_before

    for step, write in enumerate(history.own_writes, start=1):
        if write.before != current:
            weave, _, _ = _apply_version(weave, write.before, LineOrigin.OTHER, -step)
            versions.append(_list_live(weave))
        weave, inserted, deleted = _apply_version(weave, write.after, LineOrigin.OWN, step)
        versions.append(_list_live(weave))
        if write.contested:
            _mark_contested(inserted, deleted)
        current = write.after
    if history.working_tree != current:
        weave, _, _ = _apply_version(weave, history.working_tree, LineOrigin.OTHER, len(history.own_writes) + 1)
        versions.append(_list_live(weave))
    return weave, versions


def _list_live(weave: list[_LineRecord]) -> list[_LineRecord]:
    return [record for record in weave if record.deleted_by is None]


def _apply_version(
    weave: list[_LineRecord],
    text: str,
    owner: LineOrigin,
    step: int,
) -> tuple[list[_LineRecord], list[_LineRecord], list[_LineRecord]]:
    """Diff the live lines against text; return the new weave and the records this step inserted and deleted.

    An own replacement links each removed record to the records that replace
    it, so a removal can be held back when its replacement is later changed.
    """
    live = _list_live(weave)
    new_lines = text.splitlines(keepends=True)
    inserts: dict[int, list[_LineRecord]] = {}
    deleted: list[_LineRecord] = []

    for tag, i1, i2, j1, j2 in _diff_opcodes([record.text for record in live], new_lines):
        replaced = tuple(live[i1:i2]) if owner == LineOrigin.OWN and tag in _DELETE_TAGS else ()
        new_records = (
            tuple(_LineRecord(new_lines[j], owner, None, replaced, (), step, j) for j in range(j1, j2))
            if tag in _INSERT_TAGS
            else ()
        )

        if tag in _DELETE_TAGS:
            for record in live[i1:i2]:
                record.deleted_by = owner
                record.replaced_by = new_records if owner == LineOrigin.OWN else ()
            deleted.extend(live[i1:i2])
        if tag in _INSERT_TAGS:
            inserts.setdefault(i2, []).extend(new_records)
    inserted = [record for group in inserts.values() for record in group]

    return _weave_inserts(weave, live, inserts), inserted, deleted


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


def _mark_contested(inserted: list[_LineRecord], deleted: list[_LineRecord]) -> None:
    """Take every change of a contested write away from this session, marked so it can be asked about."""
    for record in inserted:
        record.origin = LineOrigin.OTHER
        record.contested = True
    for record in deleted:
        record.deleted_by = LineOrigin.OTHER
        record.contested = True


def _link_base_lines(base: list[str], weave: list[_LineRecord], version: list[_LineRecord]) -> dict[int, int]:
    """Map base line indexes to the indexes of the weave records they are; a base line no record is stays out.

    `version` is a recorded version with the text of base, as it is before
    this session commits or after it commits part of its work, and base is
    its records one by one, never other lines with the same text. Otherwise
    it is the version before the first write: base is matched to it, and
    the lines committed since then by text, between the records their
    neighbours map to: first to lines of others that are still there, then
    to this session's lines, then to removed lines.
    """
    positions = {id(record): index for index, record in enumerate(weave)}
    links = {i: positions[id(version[j])] for i, j in _match_texts(base, [record.text for record in version])}

    for is_candidate in (_is_live_other, _is_live_own, _is_removed):
        _link_by_text(base, weave, links, is_candidate)
    return links


def _link_by_text(
    base: list[str],
    weave: list[_LineRecord],
    links: dict[int, int],
    is_candidate: Callable[[_LineRecord], bool],
) -> None:
    """Link base lines not yet linked to candidate records of the same text between their neighbours' records."""
    bounds = [(-1, -1), *sorted(links.items()), (len(base), len(weave))]

    for (base_start, weave_start), (base_end, weave_end) in zip(bounds, bounds[1:]):
        candidates = [index for index in range(weave_start + 1, weave_end) if is_candidate(weave[index])]
        pairs = _match_texts(base[base_start + 1 : base_end], [weave[index].text for index in candidates])

        links.update((base_start + 1 + i, candidates[j]) for i, j in pairs)


def _render_lines(base: list[str], weave: list[_LineRecord], links: dict[int, int]) -> list[str]:
    """Base with this session's live changes applied in weave order, keeping base text for every line it kept.

    A base line that is no record stays, ahead of the new own lines next to it.
    """
    lines: list[str] = []
    cursor = 0

    for index, line in enumerate(base):
        link = links.get(index)

        if link is None:
            lines.append(line)
        else:
            lines.extend(_list_new_own_lines(weave[cursor:link]))
            if weave[link].deleted_by != LineOrigin.OWN:
                lines.append(line)
            cursor = link + 1
    lines.extend(_list_new_own_lines(weave[cursor:]))
    return lines


def _list_new_own_lines(records: list[_LineRecord]) -> list[str]:
    return [record.text for record in records if _is_live_own(record)]


def _find_entangled(weave: list[_LineRecord], committed: set[int]) -> list[str]:
    """New own lines that replace another session's uncommitted line or split lines it wrote in one go, and own
    removals of base lines whose replacement another session has since removed.

    `committed` holds the ids of the records that are base lines. An own line
    already in the base adds nothing to the commit, so what it replaced no
    longer matters, even a line that has since left the base.
    """
    others = [index for index, record in enumerate(weave) if _is_live_other(record)]
    entangled: list[str] = []

    for index, record in enumerate(weave):
        is_new_own = _is_live_own(record) and id(record) not in committed
        replaces_uncommitted = any(
            replaced.origin != LineOrigin.OWN and id(replaced) not in committed for replaced in record.replaced
        )
        is_tangled_addition = is_new_own and (replaces_uncommitted or _is_splitting(weave, others, index, committed))
        is_orphaned_removal = record.deleted_by == LineOrigin.OWN and id(record) in committed and _is_orphaned(record)

        if is_tangled_addition or is_orphaned_removal:
            entangled.append(record.text)
    return entangled


def _is_live_other(record: _LineRecord) -> bool:
    return record.deleted_by is None and record.origin != LineOrigin.OWN


def _is_live_own(record: _LineRecord) -> bool:
    return record.deleted_by is None and record.origin == LineOrigin.OWN


def _is_removed(record: _LineRecord) -> bool:
    return record.deleted_by is not None


def _is_orphaned(removed: _LineRecord) -> bool:
    """Whether an own removal lost its replacement to another session, so committing it alone would halve an edit."""
    replacements = removed.replaced_by

    return any(replacement.deleted_by == LineOrigin.OTHER for replacement in replacements) and all(
        replacement.deleted_by is not None for replacement in replacements
    )


def _is_splitting(weave: list[_LineRecord], others: list[int], index: int, committed: set[int]) -> bool:
    """Whether the record at index sits between the nearest live lines of others, uncommitted and written together.

    `others` holds the indexes of the live records not written by this session, in weave order.
    """
    following = bisect.bisect_right(others, index)
    before = weave[others[following - 1]] if following > 0 else None
    after = weave[others[following]] if following < len(others) else None

    return (
        before is not None
        and after is not None
        and id(before) not in committed
        and id(after) not in committed
        and before.step == after.step
        and after.position == before.position + 1
    )


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
