from __future__ import annotations

import datetime
import glob
import json
import math
import os
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from typing import Any, Final, Literal, TypedDict, cast, get_args

Json = dict[str, Any]
State = Literal["waiting", "working", "failed", "done", "idle", "unknown"]
Verdict = Literal["needs-input", "failed", "other"]


class _ListedOptional(TypedDict, total=False):
    link: str | None
    startedBy: str | None


class ListedRow(_ListedOptional):
    sessionId: str
    title: str
    cwd: str
    isRunning: bool
    lastActivityAt: str


class SessionRow(TypedDict):
    title: str
    cwd: str
    link: str | None
    startedBy: str | None
    isRunning: bool
    lastActivityAt: str


class SessionState(TypedDict):
    state: State
    waitingFor: str | None
    isAnswerPending: bool
    reason: str | None
    since: int | None
    row: SessionRow


class Unreadable(TypedDict):
    source: str
    reason: str


class SessionStates(TypedDict):
    version: int
    at: int
    unread: list[str] | None
    sessions: dict[str, SessionState]
    unreadable: list[Unreadable]


class Places(TypedDict):
    app_sessions: str
    app_store: str
    projects: str
    running_sessions: str


class _Seen(TypedDict):
    verdict: Verdict
    isAsking: bool
    savedAt: float | None
    askedAt: str | None
    state: State
    since: int | None


class _Before(TypedDict):
    lastActivityAt: str


class Kept(TypedDict):
    version: int
    seen: dict[str, _Seen]
    before: dict[str, _Before]
    storeStamp: str | None
    unread: list[str] | None


_VERSION = 1
_UNREAD_KEY = b"epitaxy-unread-v1"
_TAIL_BYTES = 512 * 1024
_NEEDS_INPUT = ("blocked", "need_input")
_LOCAL_COMMANDS = (
    "<command-name>",
    "<local-command-",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
)
_STATES = get_args(State)
_VERDICTS = get_args(Verdict)
_WAITING: Final = "waiting"
_WORKING: Final = "working"
_FAILED: Final = "failed"
_DONE: Final = "done"
_IDLE: Final = "idle"
_UNKNOWN: Final = "unknown"
_VERDICT_NEEDS_INPUT: Final = "needs-input"
_VERDICT_OTHER: Final = "other"
_TURN_QUESTION = "turn-question"
_DIALOG = "dialog"
# The engine's own status for a running session that waits for the person.
_RUNNING_WAITING = "waiting"
_APP_STATE_ERROR = "app state unreadable or invalid JSON"
_STORE_ERROR = "unread store unreadable"
_RUNNING_FILE_ERROR = "running session fields missing or invalid"
_TRANSCRIPT_MISSING = "transcript not found"
_TRANSCRIPT_ERROR = "transcript unreadable"
_EMPTY_SEEN: _Seen = {
    "verdict": _VERDICT_OTHER,
    "isAsking": False,
    "savedAt": None,
    "askedAt": None,
    "state": _UNKNOWN,
    "since": None,
}


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = number = 0

    while True:
        char = data[offset]
        offset += 1
        number |= (char & 0x7F) << shift
        if char < 0x80:
            return number, offset
        shift += 7


def _snappy(data: bytes) -> bytes:
    _, offset = _varint(data, 0)
    output = bytearray()

    while offset < len(data):
        tag = data[offset]
        offset += 1
        kind = tag & 3

        if kind == 0:
            length = tag >> 2

            if length >= 60:
                extra = length - 59
                length = int.from_bytes(data[offset : offset + extra], "little")
                offset += extra
            length += 1
            output += data[offset : offset + length]
            offset += length
            continue
        if kind == 1:
            length = ((tag >> 2) & 7) + 4
            distance = ((tag >> 5) << 8) | data[offset]
            offset += 1
        elif kind == 2:
            length = (tag >> 2) + 1
            distance = int.from_bytes(data[offset : offset + 2], "little")
            offset += 2
        else:
            length = (tag >> 2) + 1
            distance = int.from_bytes(data[offset : offset + 4], "little")
            offset += 4
        for _ in range(length):
            output.append(output[-distance])
    return bytes(output)


def _block(data: bytes, offset: int, size: int) -> bytes:
    raw = data[offset : offset + size]

    return _snappy(raw) if data[offset + size] == 1 else raw


def _entries(block: bytes) -> Iterator[tuple[bytes, bytes]]:
    restarts = struct.unpack("<I", block[-4:])[0]
    end = len(block) - 4 - 4 * restarts
    offset, key = 0, b""

    while offset < end:
        shared, offset = _varint(block, offset)
        nonshared, offset = _varint(block, offset)
        value_length, offset = _varint(block, offset)
        key = key[:shared] + block[offset : offset + nonshared]
        offset += nonshared
        yield key, block[offset : offset + value_length]
        offset += value_length


def _table(path: str) -> Iterator[tuple[int, bytes]]:
    with open(path, "rb") as file:
        data = file.read()
    _, footer_offset = _varint(data[-48:], 0)
    _, footer_offset = _varint(data[-48:], footer_offset)
    index, footer_offset = _varint(data[-48:], footer_offset)
    size, footer_offset = _varint(data[-48:], footer_offset)

    for _, handle in _entries(_block(data, index, size)):
        offset, handle_offset = _varint(handle, 0)
        length, _ = _varint(handle, handle_offset)

        for key, value in _entries(_block(data, offset, length)):
            if _UNREAD_KEY in key:
                yield int.from_bytes(key[-8:], "little") >> 8, value


def _log(path: str) -> Iterator[tuple[int, bytes]]:
    with open(path, "rb") as file:
        data = file.read()
    record, offset = bytearray(), 0

    while offset + 7 <= len(data):
        length = struct.unpack("<H", data[offset + 4 : offset + 6])[0]
        kind = data[offset + 6]

        if length == 0 and kind == 0:
            offset = (offset // 32768 + 1) * 32768

            continue
        record += data[offset + 7 : offset + 7 + length]
        offset += 7 + length
        if kind in (1, 4):
            batch, record = bytes(record), bytearray()
            sequence = int.from_bytes(batch[:8], "little")
            count, batch_offset = struct.unpack("<I", batch[8:12])[0], 12

            for number in range(count):
                is_put = batch[batch_offset] == 1
                size, batch_offset = _varint(batch, batch_offset + 1)
                key = batch[batch_offset : batch_offset + size]
                batch_offset += size
                value = b""

                if is_put:
                    size, batch_offset = _varint(batch, batch_offset)
                    value = batch[batch_offset : batch_offset + size]
                    batch_offset += size
                if _UNREAD_KEY in key:
                    yield sequence + number, value
        if 32768 - (offset % 32768) < 7:
            offset = (offset // 32768 + 1) * 32768


def _unread(store: str) -> list[str] | None:
    with suppress(Exception):
        logs = glob.glob(os.path.join(store, "*.log"))
        tables = glob.glob(os.path.join(store, "*.ldb"))
        found = [write for path in logs for write in _log(path)]
        found = found or [write for path in tables for write in _table(path)]
        value = max(found)[1] if found else b""

        if value:
            text = (
                value[1:].decode("utf-16-le")
                if value[:1] == b"\x00"
                else value[1:].decode("latin-1")
            )
            state = json.loads(text)["state"]

            return [
                item
                for item in state.get("unreadIds", [])
                + state.get("explicitUnreadIds", [])
                if isinstance(item, str)
            ]
        else:
            return []
    return None


def _verdict(state: Json) -> Verdict:
    summary = state.get("postTurnSummary") or {}
    category = summary.get("status_category")
    wrap_up = state.get("turnWrapUp")

    if category == _FAILED:
        return _FAILED
    elif wrap_up is not None:
        asks = wrap_up.get("needsUser") is not None

        return _VERDICT_NEEDS_INPUT if asks else _VERDICT_OTHER
    else:
        return _VERDICT_NEEDS_INPUT if category in _NEEDS_INPUT else _VERDICT_OTHER


def _transcript(session_id: str, projects: str) -> str | None:
    found = glob.glob(os.path.join(projects, "*", session_id + ".jsonl"))

    return found[0] if found else None


def _tail(path: str) -> list[bytes]:
    with open(path, "rb") as file:
        file.seek(0, 2)
        size = file.tell()

        file.seek(max(0, size - _TAIL_BYTES))
        lines = file.read().split(b"\n")

    return lines[1:] if size > _TAIL_BYTES else lines


def _entry(raw: bytes) -> Json | None:
    try:
        found: Json = json.loads(raw)

        return None if found.get("isSidechain") else found
    except ValueError:
        return None


def _blocks(found: Json) -> list[Json]:
    content = (found.get("message") or {}).get("content")

    return (
        [block for block in content if isinstance(block, dict)]
        if isinstance(content, list)
        else []
    )


def _is_prompt(found: Json) -> bool:
    content = (found.get("message") or {}).get("content")
    others = [
        block.get("text", "")
        for block in _blocks(found)
        if block.get("type") != "tool_result"
    ]
    texts = [content] if isinstance(content, str) else others

    return not found.get("isMeta") and any(
        not text.lstrip().startswith(_LOCAL_COMMANDS) for text in texts
    )


def _is_question(block: Json) -> bool:
    return block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion"


def _is_asking(path: str) -> bool:
    answered: set[str | None] = set()

    for raw in reversed(_tail(path)):
        found = (
            _entry(raw)
            if b'"type":"user"' in raw or b'"type":"assistant"' in raw
            else None
        )

        if found is None:
            continue
        elif found.get("type") == "user":
            if _is_prompt(found):
                return False
            answered.update(
                block.get("tool_use_id")
                for block in _blocks(found)
                if block.get("type") == "tool_result"
            )
        elif any(
            _is_question(block) and block.get("id") not in answered
            for block in _blocks(found)
        ):
            return True
    return False


def _empty_kept() -> Kept:
    return {
        "version": _VERSION,
        "seen": {},
        "before": {},
        "storeStamp": None,
        "unread": None,
    }


def _is_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(cast(float, value))
    except OverflowError:
        return False


def _is_seen(value: object) -> bool:
    if isinstance(value, dict):
        return (
            all(key in value for key in _EMPTY_SEEN)
            and value["verdict"] in _VERDICTS
            and type(value["isAsking"]) is bool
            and (value["savedAt"] is None or _is_number(value["savedAt"]))
            and (value["askedAt"] is None or isinstance(value["askedAt"], str))
            and value["state"] in _STATES
            and (value["since"] is None or type(value["since"]) is int)
        )
    else:
        return False


def _validated_kept(value: Json) -> Kept:
    valid = (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value["version"] == _VERSION
        and isinstance(value.get("seen"), dict)
        and all(
            isinstance(key, str) and _is_seen(row) for key, row in value["seen"].items()
        )
        and isinstance(value.get("before"), dict)
        and all(
            isinstance(key, str)
            and isinstance(row, dict)
            and isinstance(row.get("lastActivityAt"), str)
            for key, row in value["before"].items()
        )
        and "storeStamp" in value
        and (value["storeStamp"] is None or isinstance(value["storeStamp"], str))
        and "unread" in value
        and (
            value["unread"] is None
            or (
                isinstance(value["unread"], list)
                and all(isinstance(item, str) for item in value["unread"])
            )
        )
    )

    return cast(Kept, value) if valid else _empty_kept()


def _home_places() -> Places:
    home = os.path.expanduser("~")
    app = os.path.join(home, "Library", "Application Support", "Claude")

    return {
        "app_sessions": os.path.join(app, "claude-code-sessions"),
        "app_store": os.path.join(app, "Local Storage", "leveldb"),
        "projects": os.path.join(home, ".claude", "projects"),
        "running_sessions": os.path.join(home, ".claude", "sessions"),
    }


def _store_stamp(store: str) -> str:
    stamps = []

    with os.scandir(store) as entries:
        for entry in entries:
            if entry.name.endswith((".log", ".ldb")):
                saved = entry.stat()

                stamps.append(f"{entry.name}:{saved.st_size}:{saved.st_mtime_ns}")
    return "|".join(sorted(stamps))


def _session_files(root: str) -> dict[str, str]:
    files = glob.glob(os.path.join(root, "*", "*", "*.json"))

    return {os.path.basename(path)[:-5]: path for path in files}


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _process_starts(pids: list[int]) -> dict[int, str]:
    if pids:
        listed = ",".join(map(str, sorted(set(pids))))
        command = ["/bin/ps", "-o", "pid=,lstart=", "-p", listed]
        # The engine writes `procStart` as `ps` prints it in UTC.
        environment = {**os.environ, "TZ": "UTC", "LC_ALL": "C"}
        ran = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
            check=False,
        )
        starts = {}

        for line in ran.stdout.splitlines():
            words = line.split()

            if len(words) == 6 and words[0].isdigit():
                starts[int(words[0])] = " ".join(words[1:])
        return starts
    else:
        return {}


def _running_file(path: str, unreadable: list[Unreadable]) -> Json | None:
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        valid = (
            isinstance(value, dict)
            and isinstance(value.get("hostSessionId"), str)
            and bool(value["hostSessionId"])
            and type(value.get("pid")) is int
            and value["pid"] > 0
            and isinstance(value.get("status"), str)
            and bool(value["status"])
        )

        if valid:
            return cast(Json, value)
        else:
            entry: Unreadable = {"source": path, "reason": _RUNNING_FILE_ERROR}

            unreadable.append(entry)
            return None
    except (OSError, ValueError) as error:
        unreadable.append({"source": path, "reason": str(error)})
        return None


def _status_time(value: object) -> float:
    if _is_number(value):
        return float(cast(float, value))
    elif isinstance(value, str):
        try:
            # Python 3.9's ISO parser needs an offset in place of the UTC suffix.
            stamp = f"{value[:-1]}+00:00" if value.endswith("Z") else value

            return datetime.datetime.fromisoformat(stamp).timestamp() * 1000
        except ValueError:
            return 0
    else:
        return 0


def _running_sessions(root: str, unreadable: list[Unreadable]) -> dict[str, Json]:
    files = [
        _running_file(path, unreadable)
        for path in glob.glob(os.path.join(root, "*.json"))
    ]
    live = [row for row in files if row is not None and _is_alive(row["pid"])]
    waiting = [row for row in live if row["status"] == "waiting"]
    found: dict[str, Json] = {}

    try:
        starts = _process_starts([row["pid"] for row in waiting])
    except (OSError, subprocess.TimeoutExpired) as error:
        starts = {}
        unreadable.append({"source": root, "reason": str(error)})
    for row in live:
        host = row["hostSessionId"]
        previous = found.get(host)
        is_newer = previous is None or _status_time(
            row.get("statusUpdatedAt")
        ) > _status_time(previous.get("statusUpdatedAt"))

        if is_newer and _is_proven_process(row, starts):
            found[host] = row
    return found


# A waiting file counts only while its process is the one that wrote it: a pid
# the system gave to another program since starts at another time.
def _is_proven_process(row: Json, starts: dict[int, str]) -> bool:
    if row["status"] == _RUNNING_WAITING:
        start = row.get("procStart")

        return isinstance(start, str) and starts.get(row["pid"]) == " ".join(
            start.split()
        )
    else:
        return True


def _load_app_state(path: str) -> Json | None:
    with open(path, encoding="utf-8") as file:
        state = json.load(file)

    return cast(Json, state) if isinstance(state, dict) else None


def _unreadable_state(
    path: str, seen: _Seen, unreadable: list[Unreadable]
) -> tuple[_Seen, bool, str | None]:
    entry: Unreadable = {"source": path, "reason": _APP_STATE_ERROR}

    unreadable.append(entry)
    return seen, False, _APP_STATE_ERROR


def _read_session(
    listed: ListedRow,
    path: str,
    last: _Seen,
    before: _Before | None,
    projects: str,
    unreadable: list[Unreadable],
) -> tuple[_Seen, bool, str | None]:
    seen = last.copy()

    try:
        saved_at = os.stat(path).st_mtime_ns / 1_000_000

        if listed["isRunning"]:
            # A running read must not hide a final verdict saved at the same mtime.
            seen["savedAt"] = None
            state = _load_app_state(path)

            if state is not None:
                return _read_running(listed, state, seen, before, projects, unreadable)
            else:
                return _unreadable_state(path, seen, unreadable)
        elif saved_at == seen["savedAt"]:
            if seen["state"] == _UNKNOWN:
                return _unreadable_state(path, seen, unreadable)
            else:
                return seen, True, None
        else:
            seen["savedAt"] = saved_at
            state = _load_app_state(path)

            if state is not None:
                seen["verdict"] = _verdict(state)
                return seen, True, None
            else:
                return _unreadable_state(path, seen, unreadable)
    except (OSError, ValueError, AttributeError, TypeError) as error:
        entry: Unreadable = {"source": path, "reason": str(error)}

        unreadable.append(entry)
        return seen, False, _APP_STATE_ERROR


# A running session's transcript is read for an open question only once it
# stood still for a tick, and once for each such stretch.
def _read_running(
    listed: ListedRow,
    state: Json,
    seen: _Seen,
    before: _Before | None,
    projects: str,
    unreadable: list[Unreadable],
) -> tuple[_Seen, bool, str | None]:
    transcript = _transcript(state.get("cliSessionId") or "", projects)
    reads_question = (
        before is not None
        and before["lastActivityAt"] == listed["lastActivityAt"]
        and seen["askedAt"] != listed["lastActivityAt"]
    )

    seen["verdict"] = _VERDICT_OTHER
    seen["askedAt"] = listed["lastActivityAt"] if reads_question else seen["askedAt"]
    if transcript is None:
        seen["isAsking"] = False
        return seen, True, _TRANSCRIPT_MISSING
    elif reads_question:
        seen["isAsking"], reason = _read_question(transcript, unreadable)
        return seen, True, reason
    else:
        return seen, True, None


def _read_question(path: str, unreadable: list[Unreadable]) -> tuple[bool, str | None]:
    try:
        return _is_asking(path), None
    except (OSError, ValueError, AttributeError, TypeError) as error:
        reason = (
            _TRANSCRIPT_MISSING
            if isinstance(error, FileNotFoundError)
            else _TRANSCRIPT_ERROR
        )
        entry: Unreadable = {"source": path, "reason": str(error)}

        unreadable.append(entry)
        return False, reason


def _running_state(seen: _Seen, running: Json | None) -> tuple[State, str | None]:
    if running is not None and running["status"] == _RUNNING_WAITING:
        waiting_for = running.get("waitingFor")

        return _WAITING, waiting_for if isinstance(waiting_for, str) else None
    elif seen["isAsking"]:
        return _WAITING, _DIALOG
    else:
        return _WORKING, None


def _stopped_state(seen: _Seen, is_unread: bool) -> tuple[State, str | None]:
    if is_unread and seen["verdict"] == _VERDICT_NEEDS_INPUT:
        return _WAITING, _TURN_QUESTION
    elif is_unread and seen["verdict"] == _FAILED:
        return _FAILED, None
    elif is_unread:
        return _DONE, None
    else:
        return _IDLE, None


def _choose_state(
    listed: ListedRow,
    seen: _Seen,
    readable: bool,
    running: Json | None,
    is_unread: bool,
) -> tuple[State, str | None]:
    if readable and listed["isRunning"]:
        return _running_state(seen, running)
    elif readable:
        return _stopped_state(seen, is_unread)
    else:
        return _UNKNOWN, None


# The app's rows as the reader uses them. A row may lack a field the app left
# out, which must not stop the reading of every other session; one without an
# id names no session.
def _listed_rows(listed: list[ListedRow]) -> list[ListedRow]:
    rows: list[ListedRow] = []

    for value in listed:
        if isinstance(value, dict) and isinstance(value.get("sessionId"), str):
            row: ListedRow = {
                "sessionId": value["sessionId"],
                "title": str(value.get("title") or ""),
                "cwd": str(value.get("cwd") or ""),
                "isRunning": value.get("isRunning") is True,
                "lastActivityAt": str(value.get("lastActivityAt") or ""),
                "link": value.get("link"),
                "startedBy": value.get("startedBy"),
            }

            rows.append(row)
    return rows


def read_states(
    listed: list[ListedRow], kept: Kept, places: Places, now_ms: int
) -> tuple[SessionStates, Kept]:
    """Read only the supplied places; keep the returned memory for the next tick."""
    unreadable: list[Unreadable] = []
    memory = _empty_kept()
    files = _session_files(places["app_sessions"])
    running = _running_sessions(places["running_sessions"], unreadable)
    states: dict[str, SessionState] = {}

    try:
        stamp = _store_stamp(places["app_store"])
        memory["storeStamp"] = stamp
        memory["unread"] = (
            kept["unread"]
            if stamp == kept["storeStamp"]
            else _unread(places["app_store"])
        )
    except OSError:
        memory["unread"] = None
    if memory["unread"] is None:
        unreadable.append({"source": places["app_store"], "reason": _STORE_ERROR})
    for row in _listed_rows(listed):
        session_id = row["sessionId"]
        last = kept["seen"].get(session_id, _EMPTY_SEEN)
        path = files.get(
            session_id, os.path.join(places["app_sessions"], session_id + ".json")
        )
        seen, readable, reason = _read_session(
            row,
            path,
            last,
            kept["before"].get(session_id),
            places["projects"],
            unreadable,
        )
        state, waiting_for = _choose_state(
            row,
            seen,
            readable,
            running.get(session_id),
            session_id in (memory["unread"] or []),
        )
        since = (
            last["since"]
            if state == last["state"] and last["since"] is not None
            else now_ms
        )
        session_row: SessionRow = {
            "title": row["title"],
            "cwd": row["cwd"],
            "link": row.get("link"),
            "startedBy": row.get("startedBy"),
            "isRunning": row["isRunning"],
            "lastActivityAt": row["lastActivityAt"],
        }

        is_answer_pending = (
            not row["isRunning"] and seen["verdict"] == _VERDICT_NEEDS_INPUT
        )

        states[session_id] = {
            "state": state,
            "waitingFor": waiting_for,
            "isAnswerPending": is_answer_pending,
            "reason": reason,
            "since": since,
            "row": session_row,
        }
        memory["seen"][session_id] = {**seen, "state": state, "since": since}
        memory["before"][session_id] = {"lastActivityAt": row["lastActivityAt"]}
    return {
        "version": _VERSION,
        "at": now_ms,
        "unread": memory["unread"],
        "sessions": states,
        "unreadable": unreadable,
    }, memory


def read_shared_states(arguments: list[str], kept: Json) -> tuple[Json, Json]:
    states, memory = read_states(
        json.loads(arguments[0]),
        _validated_kept(kept),
        _home_places(),
        int(time.time() * 1000),
    )

    return dict(states), dict(memory)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "states":
        output, _ = read_states(
            json.loads(sys.argv[2]),
            _empty_kept(),
            _home_places(),
            int(time.time() * 1000),
        )
        print(json.dumps(output))
    else:
        sys.exit("usage: session_states.py states <listed-json>")
