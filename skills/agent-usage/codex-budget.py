#!/usr/bin/env python3
"""Keep Codex's weekly share for reviews first and hand the rest to implementation and research runs.

    codex-budget.py status
    codex-budget.py reserve --kind implementation --percent 5 --topic T --worktree PATH --session ID
    codex-budget.py reserve --kind research --percent 2 --topic T --folder PATH --session ID
    codex-budget.py reserve --kind review --percent 3 --topic T --session ID
    codex-budget.py update ID --state running|review-waiting|done|failed|abandoned

`status` and `reserve` print one JSON line. Nothing here spends Codex usage: the quota is read
through `outside-review/codex-limits.py`, and the review use per day comes from the Codex session
logs. Reservations live in one ledger under a file lock, so two sessions cannot hand out the same
share (user decision, 2026-11-17). Exit codes: 0 done, 2 the quota could not be read, 3 the
reservation does not fit, 4 unknown reservation.

Each run's brief starts with `Budget reservation: <id>`, and its use counts to that reservation's
kind (user decision, 2026-11-19). A helper run such as Codex's command-approval check follows its
parent run. A run without the line takes its purpose from `labels.jsonl` beside the ledger, written
once for runs before the line existed; with neither, it is review use unless it ran inside a
reserved folder.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import math
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
CLAUDE_LIMITS = Path.home() / "Library" / "Caches" / "usage-panel" / "claude-limits.json"
LEDGER_DIRECTORY = Path.home() / ".claude" / "codex-budget"
LEDGER_FILE = LEDGER_DIRECTORY / "ledger.jsonl"
LOCK_FILE = LEDGER_DIRECTORY / "ledger.lock"
LABELS_FILE = LEDGER_DIRECTORY / "labels.jsonl"
RESERVED_EXPIRY_HOURS = 24
RUNNING_EXPIRY_HOURS = 12
REVIEW_EXPIRY_HOURS = 2
EXIT_UNREADABLE = 2
EXIT_REFUSED = 3
EXIT_UNKNOWN_RESERVATION = 4
KIND_IMPLEMENTATION = "implementation"
KIND_RESEARCH = "research"
KIND_REVIEW = "review"
KIND_BUILDER = "builder"
STATE_RESERVED = "reserved"
STATE_RUNNING = "running"
STATE_REVIEW_WAITING = "review-waiting"
CLOSED_STATES = ("done", "failed", "abandoned")
_CODEX_LIMITS_SCRIPT = Path(__file__).resolve().parent.parent / "outside-review" / "codex-limits.py"
_WORKDAY_HOURS = (9, 20)
_HISTORY_DAYS = 14
_READ_DAYS = _HISTORY_DAYS + 7
_WEEK_MINUTES = 10080
_SAME_WINDOW_SECONDS = 60
_CLAUDE_CACHE_MAX_AGE_MINUTES = 30
_MINIMUM_ELAPSED_WORKDAYS = 0.5
_CACHED_INPUT_WEIGHT = 0.1
_OUTPUT_WEIGHT = 8
_DEFAULT_PERCENT_PER_WEIGHT = 1.37e-6
_EVENT_RESERVE = "reserve"
_EVENT_STATE = "state"
_EVENT_KEY = "event"
_OPEN_STATES = (STATE_RESERVED, STATE_RUNNING, STATE_REVIEW_WAITING)
_FRACTION = re.compile(r"(\.\d+)")
_RESERVATION_LINE = re.compile(r"Budget reservation:\s*(\d{8}-[0-9a-f]{8})")
_SETUP_MESSAGE_STARTS = ("<", "# AGENTS.md instructions")
_LABEL_KINDS = (KIND_IMPLEMENTATION, KIND_RESEARCH, KIND_REVIEW, KIND_BUILDER)
_SATURDAY = 5


@dataclass
class UsageEvent:
    moment: datetime
    weight: float
    cwd: str
    used_percent: float | None
    window_reset: float | None
    thread_id: str = ""


@dataclass
class SessionInfo:
    thread_id: str
    parent_thread_id: str
    reservation_id: str


@dataclass
class CodexHistory:
    events: list[UsageEvent]
    sessions: dict[str, SessionInfo]


@dataclass(frozen=True)
class Purpose:
    kind: str
    reservation_id: str = ""


@dataclass
class Reservation:
    identifier: str
    kind: str
    percent: float
    topic: str
    worktree: str
    session: str
    created: datetime
    state: str = STATE_RESERVED
    changed: datetime | None = None
    actual_percent: float | None = None


@dataclass
class ReviewHistory:
    weekday_mean: float
    weekday_max: float
    weekend_mean: float
    days: dict[date, float] = field(default_factory=dict)


@dataclass
class ClaudeLimits:
    percent: float | None
    resets: datetime | None


@dataclass
class CodexLimits:
    percent: float
    resets: datetime | None
    error: str


@dataclass
class BudgetStatus:
    available: float
    codex_percent: float
    open_reserved: float
    report: dict[str, object]


def parse_codex_limits(answer: object) -> CodexLimits:
    try:
        if not isinstance(answer, dict):
            return CodexLimits(0.0, None, "codex-limits.py printed no JSON object")
        elif "error" in answer:
            return CodexLimits(0.0, None, str(answer["error"]))
        elif "seven_day" not in answer or "seven_day_resets" not in answer:
            return CodexLimits(0.0, None, "Codex did not report its weekly window")
        else:
            return CodexLimits(float(answer["seven_day"]), _parse_moment(str(answer["seven_day_resets"])), "")
    except (TypeError, ValueError) as error:
        return CodexLimits(0.0, None, f"Codex reported an unreadable weekly window: {error}")


def read_claude_limits(percent: float | None, resets: str | None, now: datetime) -> ClaudeLimits:
    if percent is not None and resets is not None:
        return ClaudeLimits(percent, _parse_moment(resets))
    else:
        return _read_claude_cache(now)


def event_weight(previous: dict[str, int], current: dict[str, int]) -> float:
    def grown(key: str) -> int:
        return max(0, int(current.get(key, 0)) - int(previous.get(key, 0)))

    cached = grown("cached_input_tokens")
    uncached = max(0, grown("input_tokens") - cached)

    return uncached + cached * _CACHED_INPUT_WEIGHT + grown("output_tokens") * _OUTPUT_WEIGHT


def group_windows(events: list[UsageEvent]) -> list[list[UsageEvent]]:
    windows: list[list[UsageEvent]] = []

    for event in events:
        if event.window_reset is not None:
            reset = event.window_reset
            same = (window for window in windows if abs((window[0].window_reset or 0) - reset) <= _SAME_WINDOW_SECONDS)
            match = next(same, None)

            if match is None:
                windows.append([event])
            else:
                match.append(event)
    return windows


def measure_percent_per_weight(events: list[UsageEvent]) -> float | None:
    rise = 0.0
    weight = 0.0

    for window in group_windows(events)[-2:]:
        readings = [event.used_percent for event in window if event.used_percent is not None]

        rise += max(readings) - min(readings)
        weight += sum(event.weight for event in window[1:])
    if rise > 0 and weight > 0:
        return rise / weight
    else:
        return None


def resolve_purposes(
    sessions: dict[str, SessionInfo],
    reservations: dict[str, Reservation],
    labels: dict[str, str],
) -> dict[str, Purpose]:
    """Take each run's purpose from its own reservation line or label, else from the nearest parent run's.

    A run with neither is left out, so callers fall back to the folder rule for it.
    """
    purposes: dict[str, Purpose] = {}

    for thread_id in sessions:
        purpose = _find_purpose(thread_id, sessions, reservations, labels)

        if purpose is not None:
            purposes[thread_id] = purpose
    return purposes


def measure_daily_reviews(
    events: list[UsageEvent],
    implementation_folders: list[str],
    percent_per_weight: float,
    now: datetime,
    purposes: dict[str, Purpose] | None = None,
) -> ReviewHistory:
    """Average review use per weekday and weekend day, leaving out days with no Codex record at all."""
    first_day = (now - timedelta(days=_HISTORY_DAYS)).date()
    known = purposes or {}
    days: dict[date, float] = defaultdict(float)
    weekdays: list[float] = []
    weekends: list[float] = []

    for event in events:
        day = event.moment.date()

        if first_day <= day < now.date():
            is_review = _is_review_use(event, known.get(event.thread_id), implementation_folders)
            days[day] += event.weight * percent_per_weight if is_review else 0.0
    for day, spent in days.items():
        (weekends if day.weekday() >= _SATURDAY else weekdays).append(spent)
    return ReviewHistory(
        sum(weekdays) / len(weekdays) if weekdays else 0.0,
        max(weekdays) if weekdays else 0.0,
        sum(weekends) / len(weekends) if weekends else 0.0,
        dict(days),
    )


def count_remaining_work(start: datetime, end: datetime, history: ReviewHistory) -> tuple[float, float]:
    """Return the review share expected between the two moments and the workdays it covers."""
    expected = 0.0
    workdays = 0.0
    day = start.date()

    while start < end and day <= end.date():
        fraction = _work_fraction(start, end, day)
        is_weekend = day.weekday() >= _SATURDAY
        rate = history.weekend_mean if is_weekend else history.weekday_mean

        expected += fraction * rate
        if not is_weekend or history.weekend_mean > 0:
            workdays += fraction
        day += timedelta(days=1)
    return expected, workdays


def compute_review_reserve(
    now: datetime,
    codex_resets: datetime,
    claude_blocked_until: datetime | None,
    history: ReviewHistory,
) -> tuple[float, float, float]:
    start = max(now, claude_blocked_until) if claude_blocked_until else now
    expected, workdays = count_remaining_work(start, codex_resets, history)
    swing = min(1.0, workdays) * max(0.0, history.weekday_max - history.weekday_mean)

    return expected, swing, workdays


def attribute_spending(
    events: list[UsageEvent],
    reservations: dict[str, Reservation],
    purposes: dict[str, Purpose],
) -> dict[str, float]:
    """Return the usage weight each reservation spent, counting every event to one reservation at most.

    A run linked by its reservation line counts to that reservation. A run without a link counts to
    the reserved folder it ran in, the innermost one, but only for a non-review reservation that no
    run links to, since those are the reservations made before briefs carried the line.
    """
    linked = {purpose.reservation_id for purpose in purposes.values() if purpose.reservation_id}
    by_folder = [
        item
        for item in reservations.values()
        if item.kind != KIND_REVIEW and item.worktree and item.identifier not in linked
    ]
    weights: dict[str, float] = defaultdict(float)

    for event in events:
        owner = _find_spending_owner(event, purposes.get(event.thread_id), by_folder)

        if owner:
            weights[owner] += event.weight
    return dict(weights)


def unfilled_share(reservation: Reservation, spent: float, now: datetime) -> float:
    if reservation.state == STATE_REVIEW_WAITING or is_expired(reservation, now):
        return 0.0
    else:
        return max(0.0, reservation.percent - spent)


def is_expired(reservation: Reservation, now: datetime) -> bool:
    last_change = reservation.changed or reservation.created

    if reservation.kind == KIND_REVIEW:
        return now - reservation.created > timedelta(hours=REVIEW_EXPIRY_HOURS)
    elif reservation.state == STATE_RUNNING:
        return now - last_change > timedelta(hours=RUNNING_EXPIRY_HOURS)
    else:
        return reservation.state == STATE_RESERVED and now - last_change > timedelta(hours=RESERVED_EXPIRY_HOURS)


def decide_verdict(available: float, claude_blocked: bool, claude_projected: float | None) -> tuple[str, str]:
    verdict = "delegate" if available > 0 else "hold"

    if claude_blocked or (claude_projected is not None and claude_projected >= 100):
        return verdict, "offload-claude"
    elif claude_projected is None:
        return verdict, "unknown"
    else:
        return verdict, "extra-work"


def run_status(arguments: argparse.Namespace) -> int:
    now = _now_local()
    codex = _read_codex_limits()

    if codex.error:
        print(json.dumps({"verdict": "error", "error": codex.error}))
        return EXIT_UNREADABLE
    else:
        print(json.dumps(_load_status(arguments, now, codex).report, ensure_ascii=False))
        return 0


def run_reserve(arguments: argparse.Namespace) -> int:
    now = _now_local()
    codex = _read_codex_limits()

    if codex.error:
        print(json.dumps({"reserved": False, "error": codex.error}))
        return EXIT_UNREADABLE
    else:
        with _open_ledger():
            status = _load_status(arguments, now, codex)

            if _is_within_budget(arguments.kind, arguments.percent, status):
                worktree = os.path.realpath(arguments.worktree) if arguments.worktree else ""
                reservation = Reservation(
                    f"{now:%Y%m%d}-{uuid.uuid4().hex[:8]}",
                    arguments.kind,
                    arguments.percent,
                    arguments.topic,
                    worktree,
                    arguments.session,
                    now,
                )

                answer = {"reserved": True, "id": reservation.identifier, "available_before": status.available}

                _append_record({_EVENT_KEY: _EVENT_RESERVE, **_to_record(reservation)})
                print(json.dumps(answer))
                return 0
            else:
                refusal = {"reserved": False, "reason": "does not fit the spare share", "status": status.report}

                print(json.dumps(refusal, ensure_ascii=False))
                return EXIT_REFUSED


def run_update(arguments: argparse.Namespace) -> int:
    now = _now_local()

    with _open_ledger():
        reservation = _read_reservations(now).get(arguments.identifier)

        if reservation is None:
            print(json.dumps({"updated": False, "reason": "no such reservation"}))
            return EXIT_UNKNOWN_RESERVATION
        elif reservation.state in CLOSED_STATES:
            print(json.dumps({"updated": False, "reason": f"already {reservation.state}"}))
            return 0
        else:
            reservation.state = arguments.state
            reservation.changed = now
            if arguments.state in CLOSED_STATES:
                _record_actual_share(reservation, now)
            record = {_EVENT_KEY: _EVENT_STATE, **_to_record(reservation)}

            _append_record(record)
            print(json.dumps({"updated": True, **record}))
            return 0


def main() -> int:
    parser = _build_parser()
    arguments = parser.parse_args()
    runners = {"status": run_status, "reserve": run_reserve, "update": run_update}

    _check_reserve_arguments(parser, arguments)
    return runners[arguments.command](arguments)


def _now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse_moment(text: str) -> datetime:
    """Parse ISO 8601 on Python 3.9 too, whose parser takes only 3- or 6-digit fractions and no `Z`."""
    normalized = _FRACTION.sub(lambda found: (found.group(1) + "000000")[:7], text.replace("Z", "+00:00"), count=1)

    return datetime.fromisoformat(normalized).astimezone()


def _read_codex_limits() -> CodexLimits:
    try:
        completed = subprocess.run(
            [sys.executable, str(_CODEX_LIMITS_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        answer = json.loads(completed.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return CodexLimits(0.0, None, f"could not run codex-limits.py: {error}")
    return parse_codex_limits(answer)


def _read_claude_cache(now: datetime) -> ClaudeLimits:
    try:
        cache = json.loads(CLAUDE_LIMITS.read_text(encoding="utf-8"))
        measured = datetime.fromtimestamp(cache["at"] / 1000, tz=timezone.utc)
        weekly = next(limit for limit in cache["limits"] if limit.get("kind") == "seven_day")
        resets = _parse_moment(weekly["resetsAt"])
        percent = float(weekly["percentUsed"])
    except (OSError, ValueError, KeyError, StopIteration, TypeError, AttributeError):
        return ClaudeLimits(None, None)
    if now - measured > timedelta(minutes=_CLAUDE_CACHE_MAX_AGE_MINUTES) or resets <= now:
        return ClaudeLimits(None, None)
    else:
        return ClaudeLimits(percent, resets)


def _load_codex_history(since: datetime) -> CodexHistory:
    events: list[UsageEvent] = []
    sessions: dict[str, SessionInfo] = {}

    for path in sorted(CODEX_SESSIONS.rglob("*.jsonl")):
        try:
            if path.stat().st_mtime >= since.timestamp():
                session, session_events = _read_session_file(path)

                events.extend(session_events)
                if session.thread_id and session.thread_id not in sessions:
                    sessions[session.thread_id] = session
        except OSError:
            continue
    return CodexHistory(sorted(events, key=lambda event: event.moment), sessions)


def _read_session_file(path: Path) -> tuple[SessionInfo, list[UsageEvent]]:
    session = SessionInfo("", "", "")
    events: list[UsageEvent] = []
    cwd = ""
    brief_seen = False
    previous: dict[str, int] = {}

    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            record = _parse_line(line)
            payload = record.get("payload")

            if not isinstance(payload, dict):
                continue
            if record.get("type") == "session_meta":
                cwd = str(payload.get("cwd", ""))
                session.thread_id = str(payload.get("id") or "")
                session.parent_thread_id = str(payload.get("parent_thread_id") or "")
            elif payload.get("type") == "token_count":
                event = _build_usage_event(record, payload, cwd, session.thread_id, previous)

                if event is not None:
                    events.append(event[0])
                    previous = event[1]
            elif not brief_seen and (brief := _read_brief_text(payload)) is not None:
                session.reservation_id = _read_reservation_line(brief)
                brief_seen = True
    return session, events


def _read_brief_text(payload: dict[str, object]) -> str | None:
    """Return the first user text that is the run's brief, not the setup text Codex sends before it."""
    content = payload.get("content")

    if payload.get("type") == "message" and payload.get("role") == "user" and isinstance(content, list):
        texts = [part.get("text") for part in content if isinstance(part, dict)]
        briefs = [
            text
            for text in texts
            if isinstance(text, str) and not text.lstrip().startswith(_SETUP_MESSAGE_STARTS)
        ]

        return briefs[0] if briefs else None
    else:
        return None


def _read_reservation_line(brief: str) -> str:
    lines = [line.strip() for line in brief.splitlines() if line.strip()]
    found = _RESERVATION_LINE.fullmatch(lines[0]) if lines else None

    return found.group(1) if found else ""


def _read_labels() -> dict[str, str]:
    labels: dict[str, str] = {}

    try:
        lines = LABELS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        record = _parse_line(line)
        thread_id = record.get("thread_id")
        kind = record.get("kind")

        if isinstance(thread_id, str) and thread_id and kind in _LABEL_KINDS:
            labels[thread_id] = str(kind)
    return labels


def _find_purpose(
    thread_id: str,
    sessions: dict[str, SessionInfo],
    reservations: dict[str, Reservation],
    labels: dict[str, str],
) -> Purpose | None:
    lineage = _list_lineage(thread_id, sessions)
    found = (_read_own_purpose(ancestor, sessions, reservations, labels) for ancestor in lineage)

    return next((purpose for purpose in found if purpose is not None), None)


def _list_lineage(thread_id: str, sessions: dict[str, SessionInfo]) -> list[str]:
    """Return the run and its parent runs, nearest first, ending at a parent without a log or a loop."""
    lineage: list[str] = []
    current = thread_id

    while current and current not in lineage:
        lineage.append(current)
        session = sessions.get(current)
        current = session.parent_thread_id if session else ""
    return lineage


def _read_own_purpose(
    thread_id: str,
    sessions: dict[str, SessionInfo],
    reservations: dict[str, Reservation],
    labels: dict[str, str],
) -> Purpose | None:
    session = sessions.get(thread_id)
    reservation = reservations.get(session.reservation_id) if session else None
    label = labels.get(thread_id)

    if reservation is not None:
        return Purpose(reservation.kind, reservation.identifier)
    elif label:
        return Purpose(label)
    else:
        return None


def _is_review_use(event: UsageEvent, purpose: Purpose | None, folders: list[str]) -> bool:
    if purpose is None:
        return not _is_inside(event.cwd, folders)
    else:
        return purpose.kind == KIND_REVIEW


def _find_spending_owner(event: UsageEvent, purpose: Purpose | None, by_folder: list[Reservation]) -> str:
    if purpose is not None and purpose.reservation_id:
        return purpose.reservation_id
    elif purpose is not None and purpose.kind == KIND_REVIEW:
        return ""
    else:
        holders = [
            item
            for item in by_folder
            if _was_open_at(item, event.moment) and _is_inside(event.cwd, [item.worktree])
        ]
        owner = max(holders, key=lambda item: (len(item.worktree), item.created), default=None)

        return owner.identifier if owner else ""


def _was_open_at(reservation: Reservation, moment: datetime) -> bool:
    closed_at = reservation.changed if reservation.state in CLOSED_STATES else None

    return reservation.created <= moment and (closed_at is None or moment <= closed_at)


def _record_actual_share(reservation: Reservation, now: datetime) -> None:
    codex_history = _load_codex_history(now - timedelta(days=_READ_DAYS))
    rate = measure_percent_per_weight(codex_history.events) or _DEFAULT_PERCENT_PER_WEIGHT
    reservations = {**_read_all_reservations(), reservation.identifier: reservation}
    purposes = resolve_purposes(codex_history.sessions, reservations, _read_labels())
    spending = attribute_spending(codex_history.events, reservations, purposes)

    if reservation.worktree or reservation.identifier in spending:
        reservation.actual_percent = round(spending.get(reservation.identifier, 0.0) * rate, 2)


def _build_usage_event(
    record: dict[str, object],
    payload: dict[str, object],
    cwd: str,
    thread_id: str,
    previous: dict[str, int],
) -> tuple[UsageEvent, dict[str, int]] | None:
    info = payload.get("info")
    total = info.get("total_token_usage") if isinstance(info, dict) else None

    try:
        if isinstance(total, dict):
            moment = _parse_moment(str(record.get("timestamp")))
            used_percent, window_reset = _weekly_window(payload.get("rate_limits"))
            weight = event_weight(previous, total)

            return UsageEvent(moment, weight, cwd, used_percent, window_reset, thread_id), total
        else:
            return None
    except (TypeError, ValueError):
        return None


def _parse_line(line: str) -> dict[str, object]:
    try:
        record = json.loads(line)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def _weekly_window(limits: object) -> tuple[float | None, float | None]:
    windows = [limits.get(key) for key in ("primary", "secondary")] if isinstance(limits, dict) else []
    weekly = next(
        (window for window in windows if isinstance(window, dict) and window.get("window_minutes") == _WEEK_MINUTES),
        None,
    )

    if weekly is None:
        return None, None
    else:
        return float(weekly.get("used_percent", 0)), float(weekly.get("resets_at", 0))


def _is_inside(path: str, folders: list[str]) -> bool:
    resolved = os.path.realpath(path) if path else ""

    return any(resolved == folder or resolved.startswith(folder + os.sep) for folder in folders)


def _work_fraction(start: datetime, end: datetime, day: date) -> float:
    opening = datetime(day.year, day.month, day.day, _WORKDAY_HOURS[0], tzinfo=start.tzinfo)
    closing = datetime(day.year, day.month, day.day, _WORKDAY_HOURS[1], tzinfo=start.tzinfo)
    overlap = (min(end, closing) - max(start, opening)).total_seconds()

    return max(0.0, overlap) / (closing - opening).total_seconds()


def _project_claude(claude: ClaudeLimits, now: datetime) -> float | None:
    if claude.percent is None or claude.resets is None:
        return None
    else:
        window_start = claude.resets - timedelta(minutes=_WEEK_MINUTES)
        weekdays_only = ReviewHistory(1.0, 1.0, 0.0)
        elapsed = max(_MINIMUM_ELAPSED_WORKDAYS, count_remaining_work(window_start, now, weekdays_only)[1])
        remaining = count_remaining_work(now, claude.resets, weekdays_only)[1]

        return claude.percent + claude.percent / elapsed * remaining


def _is_within_budget(kind: str, percent: float, status: BudgetStatus) -> bool:
    if kind == KIND_REVIEW:
        return percent <= 100 - status.codex_percent - status.open_reserved
    else:
        return percent <= status.available


@contextmanager
def _open_ledger() -> Iterator[None]:
    LEDGER_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _to_record(reservation: Reservation) -> dict[str, object]:
    record = dataclasses.asdict(reservation)

    for name, value in record.items():
        record[name] = value.isoformat() if isinstance(value, datetime) else value
    return record


def _parse_reservation(record: dict[str, object]) -> Reservation | None:
    try:
        changed = record.get("changed")
        actual = record.get("actual_percent")

        return Reservation(
            str(record["identifier"]),
            str(record["kind"]),
            float(str(record["percent"])),
            str(record.get("topic", "")),
            str(record.get("worktree", "")),
            str(record.get("session", "")),
            _parse_moment(str(record["created"])),
            str(record.get("state", STATE_RESERVED)),
            None if changed is None else _parse_moment(str(changed)),
            None if actual is None else float(str(actual)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _apply_record(reservations: dict[str, Reservation], record: dict[str, object]) -> None:
    reservation = _parse_reservation(record)
    is_known = reservation is not None and reservation.identifier in reservations

    if reservation is not None and (record.get(_EVENT_KEY) == _EVENT_RESERVE or is_known):
        reservations[reservation.identifier] = reservation


def _append_record(record: dict[str, object]) -> None:
    with LEDGER_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_all_reservations() -> dict[str, Reservation]:
    """Every reservation in the ledger, for telling a run's purpose however long ago it ran."""
    reservations: dict[str, Reservation] = {}

    if LEDGER_FILE.exists():
        for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
            _apply_record(reservations, _parse_line(line))
    return reservations


def _read_reservations(now: datetime) -> dict[str, Reservation]:
    cutoff = now - timedelta(days=_HISTORY_DAYS)

    return {key: value for key, value in _read_all_reservations().items() if (value.changed or value.created) >= cutoff}


def _compute_status(
    codex_percent: float,
    codex_resets: datetime,
    claude: ClaudeLimits,
    events: list[UsageEvent],
    reservations: dict[str, Reservation],
    now: datetime,
    purposes: dict[str, Purpose],
) -> BudgetStatus:
    percent_per_weight = measure_percent_per_weight(events) or _DEFAULT_PERCENT_PER_WEIGHT
    folders = [os.path.realpath(item.worktree) for item in reservations.values() if item.worktree]
    history = measure_daily_reviews(events, folders, percent_per_weight, now, purposes)
    spending = attribute_spending(events, reservations, purposes)
    claude_blocked = claude.percent is not None and claude.percent >= 100
    blocked_until = claude.resets if claude_blocked else None
    expected, swing, workdays = compute_review_reserve(now, codex_resets, blocked_until, history)
    cutoff = now - timedelta(days=_HISTORY_DAYS)
    open_items = [
        item
        for item in reservations.values()
        if item.state in _OPEN_STATES and (item.changed or item.created) >= cutoff
    ]
    unfilled = {
        item.identifier: unfilled_share(item, spending.get(item.identifier, 0.0) * percent_per_weight, now)
        for item in open_items
    }
    open_total = sum(unfilled.values())
    available = 100 - codex_percent - open_total - expected - swing
    claude_projected = _project_claude(claude, now)
    verdict, priority = decide_verdict(available, claude_blocked, claude_projected)
    current_window = (group_windows(events) or [[]])[-1]
    current_window_weight = sum(event.weight for event in current_window)
    unknown_weight = sum(event.weight for event in current_window if event.thread_id not in purposes)
    report: dict[str, object] = {
        "verdict": verdict,
        "priority": priority,
        "available": round(available, 1),
        "pace_today": round(available / max(1.0, workdays), 1),
        "codex_percent": codex_percent,
        "codex_resets": codex_resets.isoformat(timespec="minutes"),
        "open_reserved": round(open_total, 1),
        "review_reserve": round(expected, 1),
        "review_swing": round(swing, 1),
        "workdays_left": round(workdays, 2),
        "review_per_weekday": round(history.weekday_mean, 1),
        "review_per_weekend_day": round(history.weekend_mean, 1),
        "percent_per_million": round(percent_per_weight * 1e6, 3),
        "unattributed_percent": round(max(0.0, codex_percent - current_window_weight * percent_per_weight), 1),
        "purpose_unknown_percent": round(unknown_weight * percent_per_weight, 1),
        "claude_percent": claude.percent,
        "claude_projected": None if claude_projected is None else round(claude_projected),
        "claude_blocked": claude_blocked,
        "claude_unknown": claude.percent is None,
        "open_reservations": [
            {
                "id": item.identifier,
                "kind": item.kind,
                "topic": item.topic,
                "state": item.state,
                "percent": item.percent,
                "unfilled": round(unfilled[item.identifier], 1),
            }
            for item in open_items
        ],
    }

    return BudgetStatus(round(available, 1), codex_percent, open_total, report)


def _load_status(arguments: argparse.Namespace, now: datetime, codex: CodexLimits) -> BudgetStatus:
    claude = read_claude_limits(arguments.claude_percent, arguments.claude_resets, now)
    codex_history = _load_codex_history(now - timedelta(days=_READ_DAYS))
    reservations = _read_all_reservations()
    purposes = resolve_purposes(codex_history.sessions, reservations, _read_labels())

    return _compute_status(
        codex.percent,
        codex.resets or now,
        claude,
        codex_history.events,
        reservations,
        now,
        purposes,
    )


def _positive_percent(text: str) -> float:
    value = float(text)

    if math.isfinite(value) and value > 0:
        return value
    else:
        raise argparse.ArgumentTypeError("the share must be a positive number of percent")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    reserve = commands.add_parser("reserve")
    update = commands.add_parser("update")

    for command in (status, reserve):
        command.add_argument("--claude-percent", type=float)
        command.add_argument("--claude-resets")
    reserve.add_argument("--kind", choices=(KIND_IMPLEMENTATION, KIND_RESEARCH, KIND_REVIEW), required=True)
    reserve.add_argument("--percent", type=_positive_percent, required=True)
    reserve.add_argument("--topic", required=True)
    reserve.add_argument("--worktree", "--folder", dest="worktree", default="")
    reserve.add_argument("--session", default="")
    update.add_argument("identifier")
    update.add_argument("--state", choices=(STATE_RUNNING, STATE_REVIEW_WAITING, *CLOSED_STATES), required=True)
    return parser


def _check_reserve_arguments(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    if arguments.command == "reserve" and arguments.kind != KIND_REVIEW and not arguments.worktree:
        parser.error(f"a {arguments.kind} reservation needs --worktree or --folder, the folder its Codex run works in")


if __name__ == "__main__":
    sys.exit(main())
