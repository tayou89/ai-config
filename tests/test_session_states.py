from __future__ import annotations

import builtins
import copy
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from scripts import session_states as reader

_ROOT = Path(__file__).resolve().parents[1]
_NOW = 1_800_000_000_000
_START = "Wed Nov 18 01:02:03 2026"
_QUESTION = {
    "type": "assistant",
    "message": {
        "content": [{"type": "tool_use", "name": "AskUserQuestion", "id": "ask"}]
    },
}
_RESULT = {
    "type": "user",
    "message": {
        "content": [{"type": "tool_result", "tool_use_id": "ask", "content": "yes"}]
    },
}
_PROMPT = {"type": "user", "message": {"content": "Please continue"}}
_ASKS = {"turnWrapUp": {"needsUser": "Choose one"}}
_FAILED = {"postTurnSummary": {"status_category": "failed"}, **_ASKS}

# Frozen from e5a8547's SESSIONS_READER: commit checks run a copy without .git.
_OLD_SESSIONS_READER = r"""
import json, os, glob, struct, sys, datetime
PROJECTS = os.path.expanduser('~/.claude/projects')
UNREAD_KEY = b'epitaxy-unread-v1'
TAIL_BYTES = 512 * 1024
NEEDS_INPUT = ('blocked', 'need_input')
LOCAL_COMMANDS = ('<command-name>', '<local-command-', '<bash-input>', '<bash-stdout>', '<bash-stderr>')

def varint(b, i):
    shift = n = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if c < 0x80:
            return n, i
        shift += 7

def snappy(b):
    _, i = varint(b, 0)
    out = bytearray()
    while i < len(b):
        tag = b[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            ln = tag >> 2
            if ln >= 60:
                extra = ln - 59
                ln = int.from_bytes(b[i:i + extra], 'little')
                i += extra
            ln += 1
            out += b[i:i + ln]
            i += ln
            continue
        if kind == 1:
            ln = ((tag >> 2) & 7) + 4
            off = ((tag >> 5) << 8) | b[i]
            i += 1
        elif kind == 2:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[i:i + 2], 'little')
            i += 2
        else:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[i:i + 4], 'little')
            i += 4
        for _ in range(ln):
            out.append(out[-off])
    return bytes(out)

def block(data, off, size):
    raw = data[off:off + size]
    return snappy(raw) if data[off + size] == 1 else raw

def entries(blk):
    restarts = struct.unpack('<I', blk[-4:])[0]
    end = len(blk) - 4 - 4 * restarts
    i, key = 0, b''
    while i < end:
        shared, i = varint(blk, i)
        non, i = varint(blk, i)
        vlen, i = varint(blk, i)
        key = key[:shared] + blk[i:i + non]
        i += non
        yield key, blk[i:i + vlen]
        i += vlen

# Each write of the key in a table, as (sequence number, value).
def table(path):
    data = open(path, 'rb').read()
    _, j = varint(data[-48:], 0)
    _, j = varint(data[-48:], j)
    index, j = varint(data[-48:], j)
    size, j = varint(data[-48:], j)
    for _, handle in entries(block(data, index, size)):
        offset, k = varint(handle, 0)
        length, _ = varint(handle, k)
        for key, value in entries(block(data, offset, length)):
            if UNREAD_KEY in key:
                yield int.from_bytes(key[-8:], 'little') >> 8, value

# Each write of the key in the write-ahead log, which holds the newest ones.
def log(path):
    data = open(path, 'rb').read()
    record, i = bytearray(), 0
    while i + 7 <= len(data):
        length, kind = struct.unpack('<H', data[i + 4:i + 6])[0], data[i + 6]
        if length == 0 and kind == 0:
            i = (i // 32768 + 1) * 32768
            continue
        record += data[i + 7:i + 7 + length]
        i += 7 + length
        if kind in (1, 4):
            batch, record = bytes(record), bytearray()
            seq, count, k = int.from_bytes(batch[:8], 'little'), struct.unpack('<I', batch[8:12])[0], 12
            for n in range(count):
                is_put = batch[k] == 1
                size, k = varint(batch, k + 1)
                key, k = batch[k:k + size], k + size
                value = b''
                if is_put:
                    size, k = varint(batch, k)
                    value, k = batch[k:k + size], k + size
                if UNREAD_KEY in key:
                    yield seq + n, value
        if 32768 - (i % 32768) < 7:
            i = (i // 32768 + 1) * 32768

# The sessions the sidebar marks unread, and those marked unread by hand. None
# when the store cannot be read, as after the app changed how it keeps it.
def unread(store):
    try:
        found = [w for p in glob.glob(os.path.join(store, '*.log')) for w in log(p)]
        found = found or [w for p in glob.glob(os.path.join(store, '*.ldb')) for w in table(p)]
        value = max(found)[1] if found else b''
        if not value:
            return []
        text = value[1:].decode('utf-16-le') if value[:1] == b'\x00' else value[1:].decode('latin-1')
        state = json.loads(text)['state']
        return [i for i in state.get('unreadIds', []) + state.get('explicitUnreadIds', []) if isinstance(i, str)]
    except Exception:
        return None

# As the app reads a finished turn for its sidebar: the turn's own wrap-up,
# when it made one, says whether the person is needed; else the app's summary.
def verdict(state):
    summary = state.get('postTurnSummary') or {}
    category = summary.get('status_category')
    wrap_up = state.get('turnWrapUp')
    if category == 'failed':
        return 'failed'
    if wrap_up is not None:
        return 'needs-input' if wrap_up.get('needsUser') is not None else 'other'
    return 'needs-input' if category in NEEDS_INPUT else 'other'

def transcript(session_id):
    found = glob.glob(os.path.join(PROJECTS, '*', session_id + '.jsonl'))
    return found[0] if found else None

def tail(path):
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().split(b'\n')
    return lines[1:] if size > TAIL_BYTES else lines

def entry(raw):
    try:
        found = json.loads(raw)
    except ValueError:
        return None
    return None if found.get('isSidechain') else found

def blocks(found):
    content = (found.get('message') or {}).get('content')
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []

# A user entry that started a turn: not the engine's own note, not tool
# results alone, not a command run in the app.
def is_prompt(found):
    content = (found.get('message') or {}).get('content')
    others = [c.get('text', '') for c in blocks(found) if c.get('type') != 'tool_result']
    texts = [content] if isinstance(content, str) else others
    return not found.get('isMeta') and any(not t.lstrip().startswith(LOCAL_COMMANDS) for t in texts)

def is_question(c):
    return c.get('type') == 'tool_use' and c.get('name') == 'AskUserQuestion'

# An AskUserQuestion call with no result yet, before any prompt since.
def is_asking(path):
    answered = set()
    for raw in reversed(tail(path)):
        found = entry(raw) if b'"type":"user"' in raw or b'"type":"assistant"' in raw else None
        if found is None:
            continue
        if found.get('type') == 'user':
            if is_prompt(found):
                return False
            answered.update(c.get('tool_use_id') for c in blocks(found) if c.get('type') == 'tool_result')
        elif any(is_question(c) and c.get('id') not in answered for c in blocks(found)):
            return True
    return False

def session(asked):
    state = json.load(open(asked['file'], encoding='utf-8'))
    path = transcript(state.get('cliSessionId') or '') if asked['readsQuestion'] else None
    return {'verdict': verdict(state), 'isAsking': path is not None and is_asking(path)}

def sessions(request):
    out = {'sessions': {}}
    if 'store' in request:
        out['unread'] = unread(request['store'])
    for asked in request['sessions']:
        try:
            out['sessions'][asked['id']] = session(asked)
        except (OSError, ValueError):
            continue
    return out

def ms(stamp):
    return int(datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000)

# This session's newest reply, and how long the newest cache it wrote lives: a
# reply served from the cache alone wrote none.
def cache(session_id):
    path = transcript(session_id)
    out = {}
    for raw in reversed(tail(path) if path else []):
        found = entry(raw) if b'"type":"assistant"' in raw and b'"usage"' in raw else None
        message = (found or {}).get('message') or {}
        if not message.get('usage') or message.get('model') == '<synthetic>':
            continue
        out.setdefault('at', ms(found['timestamp']))
        written = message['usage'].get('cache_creation') or {}
        if written.get('ephemeral_1h_input_tokens') or written.get('ephemeral_5m_input_tokens'):
            out['ttl'] = '1h' if written.get('ephemeral_1h_input_tokens') else '5m'
            break
    return out

if sys.argv[1] == 'cache':
    print(json.dumps(cache(sys.argv[2])))
else:
    print(json.dumps(sessions(json.loads(sys.argv[2]))))
"""


def _varint(number: int) -> bytes:
    output = bytearray()

    while number >= 128:
        output.append((number & 127) | 128)
        number >>= 7
    output.append(number)
    return bytes(output)


def _line(entry: reader.Json) -> bytes:
    return json.dumps(entry, separators=(",", ":")).encode() + b"\n"


def _log_value(ids: list[str], explicit: list[str], utf16: bool = False) -> bytes:
    text = json.dumps({"state": {"unreadIds": ids, "explicitUnreadIds": explicit}})

    return (
        b"\x00" + text.encode("utf-16-le")
        if utf16
        else b"\x01" + text.encode("latin-1")
    )


def _batch(value: bytes, sequence: int = 1) -> bytes:
    key = b"_file://\x00\x01epitaxy-unread-v1"

    return (
        struct.pack("<QI", sequence, 1)
        + b"\x01"
        + _varint(len(key))
        + key
        + _varint(len(value))
        + value
    )


def _log_record(batch: bytes, kind: int = 1) -> bytes:
    return b"\x00" * 4 + struct.pack("<HB", len(batch), kind) + batch


def _counts(states: reader.SessionStates) -> dict[str, int]:
    return {
        state: sum(row["state"] == state for row in states["sessions"].values())
        for state in ("working", "waiting", "done")
    }


class SessionStatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self._home = Path(self._temporary.name)
        app = self._home / "Library" / "Application Support" / "Claude"
        self._places: reader.Places = {
            "app_sessions": str(app / "claude-code-sessions"),
            "app_store": str(app / "Local Storage" / "leveldb"),
            "projects": str(self._home / ".claude" / "projects"),
            "running_sessions": str(self._home / ".claude" / "sessions"),
        }
        self._listed: list[reader.ListedRow] = []
        self._kept = reader._empty_kept()
        self._now = _NOW

        for folder in self._places.values():
            Path(folder).mkdir(parents=True)
        self._write_unread([])

    def _state_path(self, session_id: str) -> Path:
        return (
            Path(self._places["app_sessions"])
            / "account"
            / "organization"
            / f"{session_id}.json"
        )

    def _transcript_path(self, session_id: str) -> Path:
        return Path(self._places["projects"]) / "project" / f"cli-{session_id}.jsonl"

    def _write_unread(self, ids: list[str], explicit: list[str] | None = None) -> None:
        path = Path(self._places["app_store"]) / "000003.log"

        path.write_bytes(_log_record(_batch(_log_value(ids, explicit or []))))

    def _add(
        self,
        state: reader.Json | None = None,
        running: bool = False,
        entries: list[reader.Json] | None = None,
        session_id: str = "local_one",
    ) -> reader.ListedRow:
        path = self._state_path(session_id)
        transcript = self._transcript_path(session_id)
        row: reader.ListedRow = {
            "sessionId": session_id,
            "title": f"Title {session_id}",
            "cwd": "/fixture/project",
            "isRunning": running,
            "lastActivityAt": "2026-11-18T01:00:00Z",
            "link": f"claude://session/{session_id}",
            "startedBy": "local_parent",
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cliSessionId": f"cli-{session_id}", **(state or {})})
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_bytes(b"".join(_line(entry) for entry in entries or []))
        self._listed.append(row)
        return row

    def _read(self) -> reader.SessionStates:
        output, self._kept = reader.read_states(
            self._listed, self._kept, self._places, self._now
        )

        self._now += 5000
        return output

    def _settled(self) -> reader.SessionStates:
        self._read()
        return self._read()

    def _one(self, output: reader.SessionStates) -> reader.SessionState:
        return output["sessions"]["local_one"]

    def _running(
        self,
        name: str = "engine",
        waiting_for: str = "permission prompt",
        updated: int = 1,
        session_id: str = "local_one",
        status: str = "waiting",
    ) -> Path:
        path = Path(self._places["running_sessions"]) / f"{name}.json"
        row = {
            "hostSessionId": session_id,
            "pid": 12345,
            "procStart": _START,
            "status": status,
            "waitingFor": waiting_for,
            "statusUpdatedAt": updated,
        }

        path.write_text(json.dumps(row))
        return path

    def _live(self) -> None:
        alive = patch.object(reader, "_is_alive", return_value=True)
        starts = patch.object(reader, "_process_starts", return_value={12345: _START})

        alive.start()
        starts.start()
        self.addCleanup(alive.stop)
        self.addCleanup(starts.stop)

    def test_01_unread_wrap_up_asks(self) -> None:
        self._add(_ASKS)
        self._write_unread(["local_one"])
        state = self._one(self._read())

        self.assertEqual(
            (state["state"], state["waitingFor"]), ("waiting", "turn-question")
        )
        self.assertTrue(state["isAnswerPending"])

    def test_02_read_wrap_up_asks(self) -> None:
        self._add(_ASKS)
        state = self._one(self._read())

        self.assertEqual(state["state"], "idle")
        self.assertTrue(state["isAnswerPending"])

    def test_03_wrap_up_without_question(self) -> None:
        self._add({"turnWrapUp": {}, "postTurnSummary": {"status_category": "blocked"}})
        self._write_unread(["local_one"])
        self.assertEqual(self._one(self._read())["state"], "done")

    def test_04_summary_needs_input(self) -> None:
        for category in ("blocked", "need_input"):
            with self.subTest(category=category):
                self._listed = []
                self._kept = reader._empty_kept()
                self._add({"postTurnSummary": {"status_category": category}})
                self._write_unread(["local_one"])
                state = self._one(self._read())

                self.assertEqual(
                    (state["state"], state["waitingFor"]), ("waiting", "turn-question")
                )

    def test_05_summary_other(self) -> None:
        self._add({"postTurnSummary": {"status_category": "completed"}})
        self._write_unread(["local_one"])
        self.assertEqual(self._one(self._read())["state"], "done")

    def test_06_failed_overrides_wrap_up(self) -> None:
        self._add(_FAILED)
        self._write_unread(["local_one"])
        state = self._one(self._read())

        self.assertEqual(state["state"], "failed")
        self.assertFalse(state["isAnswerPending"])

    def test_07_read_failed(self) -> None:
        self._add(_FAILED)
        self.assertEqual(self._one(self._read())["state"], "idle")

    def test_08_running_ignores_old_verdict(self) -> None:
        self._add(_FAILED, running=True)
        self._add(_ASKS, running=True, session_id="local_asks")
        self._write_unread([row["sessionId"] for row in self._listed])

        for state in self._read()["sessions"].values():
            self.assertEqual(state["state"], "working")
            self.assertFalse(state["isAnswerPending"])

    def test_09_unanswered_question_after_still_tick(self) -> None:
        self._add(running=True, entries=[_QUESTION])
        self.assertEqual(self._one(self._read())["state"], "working")
        state = self._one(self._read())

        self.assertEqual((state["state"], state["waitingFor"]), ("waiting", "dialog"))

    def test_10_question_result_arrives(self) -> None:
        row = self._add(running=True, entries=[_QUESTION])

        self.assertEqual(self._one(self._settled())["state"], "waiting")
        self._transcript_path("local_one").write_bytes(
            _line(_QUESTION) + _line(_RESULT)
        )
        row["lastActivityAt"] = "2026-11-18T01:01:00Z"
        self.assertEqual(self._one(self._settled())["state"], "working")

    def test_11_immediate_hook_refusal(self) -> None:
        result = copy.deepcopy(_RESULT)
        result["message"]["content"][0]["is_error"] = True

        self._add(running=True, entries=[_QUESTION, result])
        self.assertEqual(self._one(self._settled())["state"], "working")

    def test_12_real_prompt_and_non_prompts(self) -> None:
        ignored = [
            {**_PROMPT, "isMeta": True},
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "other"}]
                },
            },
            *[
                {"type": "user", "message": {"content": prefix + "test"}}
                for prefix in reader._LOCAL_COMMANDS
            ],
        ]

        self._add(running=True, entries=[_QUESTION, *ignored])
        self.assertEqual(self._one(self._settled())["state"], "waiting")
        self._transcript_path("local_one").write_bytes(
            b"".join(_line(row) for row in [_QUESTION, *ignored, _PROMPT])
        )
        self._listed[0]["lastActivityAt"] = "2026-11-18T01:01:00Z"
        self.assertEqual(self._one(self._settled())["state"], "working")

    def test_13_sidechain_question_ignored(self) -> None:
        self._add(running=True, entries=[{**_QUESTION, "isSidechain": True}])
        self.assertEqual(self._one(self._settled())["state"], "working")

    def test_14_permission_wait(self) -> None:
        self._live()
        self._add(running=True)
        self._running()
        state = self._one(self._read())

        self.assertEqual(
            (state["state"], state["waitingFor"]), ("waiting", "permission prompt")
        )

    def test_15_dead_pid_ignored(self) -> None:
        self._add(running=True)
        self._running()

        with patch.object(reader.os, "kill", side_effect=ProcessLookupError):
            self.assertEqual(self._one(self._read())["state"], "working")

    def test_16_reused_pid_ignored(self) -> None:
        self._add(running=True)
        self._running()

        with (
            patch.object(reader, "_is_alive", return_value=True),
            patch.object(
                reader, "_process_starts", return_value={12345: "other start"}
            ),
        ):
            self.assertEqual(self._one(self._read())["state"], "working")

    def test_17_waiting_for_verbatim(self) -> None:
        self._live()
        self._add(running=True)

        for value in (
            "dialog open",
            "input needed",
            "sandbox request",
            "goal proposal",
            "future engine value",
        ):
            with self.subTest(value=value):
                self._running(waiting_for=value)
                state = self._one(self._read())

                self.assertEqual(
                    (state["state"], state["waitingFor"]), ("waiting", value)
                )

    def test_18_latest_live_file_wins(self) -> None:
        self._live()
        self._add(running=True)
        self._running("older", updated=1)
        self._running("newer", waiting_for="sandbox request", updated=2)

        self.assertEqual(self._one(self._read())["waitingFor"], "sandbox request")
        self._running("latest", updated=3, status="running")
        self.assertEqual(self._one(self._read())["state"], "working")

    def test_19_permission_beats_dialog(self) -> None:
        self._live()
        self._add(running=True, entries=[_QUESTION])
        self._running()

        self.assertEqual(self._one(self._settled())["waitingFor"], "permission prompt")

    def test_20_partial_tail_line_dropped(self) -> None:
        self._add(running=True)
        path = self._transcript_path("local_one")
        suffix = _line(_QUESTION) + b" " * (reader._TAIL_BYTES - len(_line(_QUESTION)))

        path.write_bytes(b"prefix" + suffix)
        self.assertEqual(reader._tail(str(path)), [suffix.split(b"\n")[1]])
        self.assertEqual(self._one(self._settled())["state"], "working")
        path.write_bytes(b"x" * reader._TAIL_BYTES + b"\n" + _line(_QUESTION))
        self._listed[0]["lastActivityAt"] = "2026-11-18T01:01:00Z"
        self.assertEqual(self._one(self._settled())["state"], "waiting")

    def test_21_missing_transcript(self) -> None:
        self._add(running=True)
        self._transcript_path("local_one").unlink()
        state = self._one(self._settled())

        self.assertEqual(state["state"], "working")
        self.assertEqual(state["reason"], "transcript not found")

    def test_22_broken_app_state(self) -> None:
        self._live()
        self._add(running=True, entries=[_QUESTION])
        self._running()
        path = self._state_path("local_one")

        for content in ("broken", "[]"):
            with self.subTest(content=content):
                path.write_text(content)
                output = self._read()

                self.assertEqual(self._one(output)["state"], "unknown")
                self.assertIn(
                    str(path), [item["source"] for item in output["unreadable"]]
                )
        path.unlink()
        self.assertEqual(self._one(self._read())["state"], "unknown")

    def test_23_unread_store_unreadable(self) -> None:
        self._live()
        self._add(_ASKS)
        self._add(_FAILED, session_id="local_failed")
        self._add(session_id="local_done")
        self._add(running=True, entries=[_QUESTION], session_id="local_dialog")
        self._add(running=True, session_id="local_permission")
        self._running(session_id="local_permission")
        (Path(self._places["app_store"]) / "000003.log").write_bytes(
            _log_record(b"bad")
        )
        output = self._settled()

        self.assertIsNone(output["unread"])
        self.assertEqual(
            [state["state"] for state in output["sessions"].values()],
            ["idle", "idle", "idle", "waiting", "waiting"],
        )
        self.assertEqual(output["sessions"]["local_dialog"]["waitingFor"], "dialog")
        self.assertEqual(
            output["sessions"]["local_permission"]["waitingFor"], "permission prompt"
        )
        self.assertIn(
            self._places["app_store"], [item["source"] for item in output["unreadable"]]
        )

    def test_24_archived_session_not_listed(self) -> None:
        self._add(session_id="local_archived")
        self._listed = []
        self._write_unread(["local_archived"])

        self.assertEqual(self._read()["sessions"], {})

    def test_row_missing_fields_still_read(self) -> None:
        self._add(_ASKS)
        self._add(session_id="local_two")
        self._write_unread(["local_one"])
        partial = cast(Any, self._listed[1])

        for field in ("title", "cwd", "lastActivityAt", "isRunning"):
            del partial[field]
        self._listed.append(cast(reader.ListedRow, {"title": "no id"}))
        output = self._read()

        self.assertEqual(self._one(output)["state"], "waiting")
        self.assertEqual(output["sessions"]["local_two"]["state"], "idle")
        self.assertEqual(output["sessions"]["local_two"]["row"]["title"], "")
        self.assertEqual(len(output["sessions"]), 2)

    def test_25_key_file_never_opened(self) -> None:
        secret = Path(self._places["running_sessions"]) / "engine.key"

        secret.write_text("fixture only")
        self._live()
        self._running()
        with patch("builtins.open", wraps=builtins.open) as opened:
            self._read()
        self.assertNotIn(
            str(secret), [str(call.args[0]) for call in opened.call_args_list]
        )

    def test_28_malformed_kept_starts_empty(self) -> None:
        self._add(_ASKS)
        self._write_unread(["local_one"])
        valid = self._settled()
        broken = [
            None,
            [],
            {},
            {"version": 2},
            {**self._kept, "version": True},
            {**self._kept, "seen": []},
            {**self._kept, "before": {"local_one": {}}},
            {**self._kept, "unread": [1]},
        ]

        for field, value in (
            ("verdict", []),
            ("isAsking", 1),
            ("savedAt", "yesterday"),
            ("askedAt", 1),
            ("state", {}),
            ("since", False),
        ):
            damaged = copy.deepcopy(self._kept)
            damaged["seen"]["local_one"][field] = value
            broken.append(damaged)
        with patch.dict(os.environ, {"HOME": str(self._home)}):
            for kept in broken:
                with self.subTest(kept=kept):
                    output, _ = reader.read_shared_states(
                        [json.dumps(self._listed)], kept
                    )

                    self.assertEqual(_counts(output), _counts(valid))
                    self.assertEqual(output["version"], 1)

    def test_30_old_reader_counts_match(self) -> None:
        cases = [
            (_ASKS, False, [], True),
            (_ASKS, False, [], False),
            ({"turnWrapUp": {}}, False, [], True),
            ({"postTurnSummary": {"status_category": "blocked"}}, False, [], True),
            ({"postTurnSummary": {"status_category": "need_input"}}, False, [], True),
            ({"postTurnSummary": {"status_category": "completed"}}, False, [], True),
            (_FAILED, False, [], True),
            (_FAILED, False, [], False),
            (_FAILED, True, [], True),
            (_ASKS, True, [], True),
            ({}, True, [_QUESTION], False),
            ({}, True, [_QUESTION, _RESULT], False),
            (
                {},
                True,
                [
                    _QUESTION,
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "ask",
                                    "is_error": True,
                                }
                            ]
                        },
                    },
                ],
                False,
            ),
            ({}, True, [_QUESTION, _PROMPT], False),
            ({}, True, [{**_QUESTION, "isSidechain": True}], False),
        ]
        unread = []

        for number, (state, running, entries, is_unread) in enumerate(cases):
            session_id = f"local_{number}"

            self._add(state, running, entries, session_id)
            if is_unread:
                unread.append(session_id)
        self._add(running=True, session_id="local_tail")
        self._transcript_path("local_tail").write_bytes(
            b"x" * reader._TAIL_BYTES + b"\n" + _line(_QUESTION)
        )
        self._write_unread(unread)
        request = {
            "store": self._places["app_store"],
            "sessions": [
                {
                    "id": row["sessionId"],
                    "file": str(self._state_path(row["sessionId"])),
                    "readsQuestion": row["isRunning"],
                }
                for row in self._listed
            ],
        }

        for broken_store in (False, True):
            if broken_store:
                (Path(self._places["app_store"]) / "000003.log").write_bytes(
                    _log_record(b"bad")
                )
            old = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _OLD_SESSIONS_READER,
                    "sessions",
                    json.dumps(request),
                ],
                env={**os.environ, "HOME": str(self._home)},
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(old.stdout)
            counts = {"working": 0, "waiting": 0, "done": 0}

            for row in self._listed:
                seen = result["sessions"][row["sessionId"]]
                is_unread = row["sessionId"] in (result["unread"] or [])
                is_waiting = (row["isRunning"] and seen["isAsking"]) or (
                    not row["isRunning"]
                    and is_unread
                    and seen["verdict"] == "needs-input"
                )

                if is_waiting:
                    counts["waiting"] += 1
                elif row["isRunning"]:
                    counts["working"] += 1
                elif is_unread and seen["verdict"] != "failed":
                    counts["done"] += 1
            self.assertEqual(_counts(self._settled()), counts)

    def test_unchanged_state_file_not_reopened(self) -> None:
        self._add(_ASKS)
        self._read()
        path = self._state_path("local_one")

        with patch("builtins.open", wraps=builtins.open) as opened:
            self._read()
        self.assertNotIn(
            str(path), [str(call.args[0]) for call in opened.call_args_list]
        )
        path.write_text(json.dumps({"turnWrapUp": {}}))
        with patch("builtins.open", wraps=builtins.open) as opened:
            self._read()
        self.assertIn(str(path), [str(call.args[0]) for call in opened.call_args_list])

    def test_still_running_scanned_once_per_stretch(self) -> None:
        row = self._add(running=True, entries=[_QUESTION])

        with patch.object(reader, "_is_asking", wraps=reader._is_asking) as scan:
            self._read()
            self.assertEqual(scan.call_count, 0)
            self._read()
            self._read()
            self.assertEqual(scan.call_count, 1)
            row["lastActivityAt"] = "2026-11-18T01:01:00Z"
            self._read()
            self.assertEqual(scan.call_count, 1)
            self._read()
            self._read()
            self.assertEqual(scan.call_count, 2)

    def test_unchanged_store_not_reread(self) -> None:
        with patch.object(reader, "_unread", wraps=reader._unread) as unread:
            self._read()
            self._read()
            self.assertEqual(unread.call_count, 1)
            self._write_unread(["local_one"])
            self._read()
            self.assertEqual(unread.call_count, 2)

    def test_since_and_row_carried(self) -> None:
        row = self._add(_ASKS)
        self._write_unread(["local_one"])
        first = self._one(self._read())
        unchanged = self._one(self._read())

        self.assertEqual(first["since"], _NOW)
        self.assertEqual(unchanged["since"], first["since"])
        self.assertEqual(
            first["row"],
            {key: value for key, value in row.items() if key != "sessionId"},
        )
        self._write_unread([])
        self.assertEqual(self._one(self._read())["since"], _NOW + 10000)

    def test_shared_adapter_json_round_trip(self) -> None:
        self._add(running=True, entries=[_QUESTION])
        arguments = [json.dumps(self._listed)]

        with patch.dict(os.environ, {"HOME": str(self._home)}):
            first, kept = reader.read_shared_states(arguments, {})
            second, kept = reader.read_shared_states(
                arguments, json.loads(json.dumps(kept))
            )
            third, _ = reader.read_shared_states(
                arguments, json.loads(json.dumps(kept))
            )
        self.assertEqual(self._one(first)["state"], "working")
        self.assertEqual(self._one(second)["waitingFor"], "dialog")
        self.assertEqual(self._one(second)["since"], self._one(third)["since"])

    def test_read_states_does_not_mutate_kept(self) -> None:
        self._add(running=True, entries=[_QUESTION])
        self._read()
        before = copy.deepcopy(self._kept)

        reader.read_states(self._listed, self._kept, self._places, _NOW)
        self.assertEqual(self._kept, before)

    def test_stopped_session_reads_final_verdict_at_same_mtime(self) -> None:
        row = self._add(_ASKS, running=True)
        self._write_unread(["local_one"])
        self._settled()
        row["isRunning"] = False

        self.assertEqual(self._one(self._read())["waitingFor"], "turn-question")

    def test_malformed_running_files_reported(self) -> None:
        root = Path(self._places["running_sessions"])

        for index, content in enumerate(
            (
                "not JSON",
                "[]",
                "{}",
                '{"hostSessionId":"local_one","pid":0,"status":"waiting"}',
            )
        ):
            (root / f"bad-{index}.json").write_text(content)
        self.assertEqual(len(self._read()["unreadable"]), 4)

    def test_permission_error_counts_pid_alive(self) -> None:
        with patch.object(reader.os, "kill", side_effect=PermissionError) as kill:
            self.assertTrue(reader._is_alive(12345))
        kill.assert_called_once_with(12345, 0)

    def test_one_utc_ps_for_all_waiting_files(self) -> None:
        self._running("first")
        self._running("second", updated=2)
        completed = subprocess.CompletedProcess(
            [], 0, "12345 Wed Nov  18 01:02:03 2026\n", ""
        )

        with (
            patch.object(reader.os, "kill"),
            patch.object(reader.subprocess, "run", return_value=completed) as run,
        ):
            found = reader._running_sessions(self._places["running_sessions"], [])
        self.assertEqual(found["local_one"]["statusUpdatedAt"], 2)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["env"]["TZ"], "UTC")
        self.assertEqual(run.call_args.args[0][-1], "12345")

    def test_log_latest_write_and_explicit_utf16(self) -> None:
        path = Path(self._places["app_store"]) / "000003.log"
        first = _log_record(_batch(_log_value(["old"], []), 1))
        latest = _batch(_log_value(["new"], ["explicit"], utf16=True), 2)

        path.write_bytes(
            first + _log_record(latest[:10], 2) + _log_record(latest[10:], 4)
        )
        self.assertEqual(self._read()["unread"], ["new", "explicit"])

    def test_standalone_isolated_command(self) -> None:
        self._add(_ASKS)
        self._write_unread(["local_one"])
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(_ROOT / "scripts" / "session_states.py"),
                "states",
                json.dumps(self._listed),
            ],
            env={**os.environ, "HOME": str(self._home)},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(
            self._one(json.loads(result.stdout))["waitingFor"], "turn-question"
        )

    def test_shared_runner_imports_installed_reader_and_keeps_memory(self) -> None:
        self._add(running=True, entries=[_QUESTION])
        scripts = self._home / ".claude" / "scripts"
        source = (_ROOT / "mods" / "usage-panel" / "hooks" / "register.tsx").read_text()
        runner = re.search(
            r"const SHARED_READER = String.raw`\n(.*?)\n`", source, re.DOTALL
        ).group(1)
        join = re.search(
            r"const SESSION_STATES_JOIN = String.raw`\n(.*?)\n`", source, re.DOTALL
        ).group(1)
        shared = self._home / "cache" / "session-states.json"
        arguments = [
            sys.executable,
            "-I",
            "-c",
            runner + "\n" + join,
            str(shared),
            "",
            str(10**14),
            "15",
            json.dumps(self._listed),
        ]

        scripts.mkdir()
        shutil.copyfile(
            _ROOT / "scripts" / "session_states.py", scripts / "session_states.py"
        )
        first = subprocess.run(
            arguments,
            env={**os.environ, "HOME": str(self._home)},
            capture_output=True,
            text=True,
            check=True,
        )
        second = subprocess.run(
            arguments,
            env={**os.environ, "HOME": str(self._home)},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            self._one(json.loads(first.stdout)["output"])["state"], "working"
        )
        self.assertEqual(
            self._one(json.loads(second.stdout)["output"])["waitingFor"], "dialog"
        )
        memory = json.loads(shared.with_name("session-states.scan.json").read_text())

        self.assertEqual(
            memory["seen"]["local_one"]["askedAt"], self._listed[0]["lastActivityAt"]
        )
        arguments[6] = "0"
        arguments[-1] = "not JSON: a fresh shared entry must not invoke the adapter"
        fresh = subprocess.run(
            arguments,
            env={**os.environ, "HOME": str(self._home)},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(json.loads(fresh.stdout), json.loads(second.stdout))

    def test_cache_reader_keeps_its_output(self) -> None:
        self._add()
        source = (_ROOT / "mods" / "usage-panel" / "hooks" / "register.tsx").read_text()
        script = re.search(
            r"const CACHE_READER = String.raw`\n(.*?)\n`", source, re.DOTALL
        ).group(1)
        reply = {
            "type": "assistant",
            "timestamp": "2026-11-18T01:00:00Z",
            "message": {
                "model": "fixture",
                "usage": {"cache_creation": {"ephemeral_1h_input_tokens": 1}},
            },
        }

        self._transcript_path("local_one").write_bytes(_line(reply))
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, "cli-local_one"],
            env={**os.environ, "HOME": str(self._home)},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(json.loads(result.stdout), {"at": 1794963600000, "ttl": "1h"})

    def test_table_snappy_and_all_copy_tags(self) -> None:
        key = b"epitaxy-unread-v1" + struct.pack("<Q", 7 << 8 | 1)
        value = _log_value(["local_table"], [])
        block = (
            b"\x00"
            + _varint(len(key))
            + _varint(len(value))
            + key
            + value
            + struct.pack("<II", 0, 1)
        )
        compressed = _varint(len(block)) + bytes([60 << 2, len(block) - 1]) + block
        data = compressed + b"\x01" + b"\x00" * 4
        handle = _varint(0) + _varint(len(compressed))
        index = (
            b"\x00\x01"
            + _varint(len(handle))
            + b"z"
            + handle
            + struct.pack("<II", 0, 1)
        )
        footer = b"\x00\x00" + _varint(len(data)) + _varint(len(index))
        table = (
            data
            + index
            + b"\x00" * 5
            + footer.ljust(40, b"\x00")
            + struct.pack("<Q", 0xDB4775248B80FB57)
        )
        store = Path(self._places["app_store"])

        (store / "000003.log").unlink()
        (store / "000004.ldb").write_bytes(table)
        self.assertEqual(self._read()["unread"], ["local_table"])
        self.assertEqual(
            reader._snappy(b"\x0d\x08abc\x01\x03\x0a\x03\x00\x0b\x03\x00\x00\x00"),
            b"abcabcabcabca",
        )

    def test_ps_failure_leaves_working_and_reports_source(self) -> None:
        self._add(running=True)
        self._running()

        with (
            patch.object(reader, "_is_alive", return_value=True),
            patch.object(
                reader, "_process_starts", side_effect=OSError("fixture ps failure")
            ),
        ):
            output = self._read()
        self.assertEqual(self._one(output)["state"], "working")
        self.assertIn(
            self._places["running_sessions"],
            [item["source"] for item in output["unreadable"]],
        )

    def test_transcript_failure_does_not_make_app_state_unknown(self) -> None:
        self._live()
        self._add(running=True, entries=[_QUESTION])

        with patch.object(
            reader,
            "_is_asking",
            side_effect=PermissionError("fixture transcript failure"),
        ):
            output = self._settled()
        self.assertEqual(self._one(output)["state"], "working")
        self.assertEqual(self._one(output)["reason"], "transcript unreadable")
        self.assertIn(
            str(self._transcript_path("local_one")),
            [item["source"] for item in output["unreadable"]],
        )
        self._running()
        self._listed[0]["lastActivityAt"] = "2026-11-18T01:01:00Z"
        with patch.object(reader, "_is_asking", side_effect=FileNotFoundError):
            output = self._settled()
        self.assertEqual(self._one(output)["waitingFor"], "permission prompt")

    def test_lost_transcript_drops_cached_dialog(self) -> None:
        self._add(running=True, entries=[_QUESTION])

        self.assertEqual(self._one(self._settled())["waitingFor"], "dialog")
        self._transcript_path("local_one").unlink()
        state = self._one(self._read())

        self.assertEqual(state["state"], "working")
        self.assertEqual(state["reason"], "transcript not found")


if __name__ == "__main__":
    unittest.main()
