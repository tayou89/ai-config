"""The shared readers inside hooks/register.tsx, run as the mod runs them, on records made here.

Run with: /usr/bin/python3 -m unittest tests/readers_test.py
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from typing import Any

REGISTER = os.path.join(os.path.dirname(__file__), "..", "hooks", "register.tsx")
PYTHON = "/usr/bin/python3"
LIMIT_SECONDS = "30"
EVER = "0"
NOW_ONLY = str(10**14)
LOCAL = ("local",)


def _read_register() -> str:
    with open(REGISTER, encoding="utf-8") as file:
        return file.read()


def _script(source: str, name: str) -> str:
    found = re.search(rf"^const {name} = String\.raw`\n(.*?)^`", source, re.S | re.M)

    if found is None:
        raise LookupError(f"{name} is not in register.tsx")
    else:
        return found.group(1)


def _busy_code(source: str) -> int:
    found = re.search(r"^const SHARED_BUSY = (\d+)$", source, re.M)

    if found is None:
        raise LookupError("SHARED_BUSY is not in register.tsx")
    else:
        return int(found.group(1))


SOURCE = _read_register()
SHARED_READER = _script(SOURCE, "SHARED_READER")
HISTORY = f"{SHARED_READER}\n\n{_script(SOURCE, 'HISTORY_READER')}"
CODEX = f"{SHARED_READER}\n\n{_script(SOURCE, 'CODEX_READER')}"
BUSY = _busy_code(SOURCE)


def _stamp(minutes_ago: int) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)

    return moment.isoformat().replace("+00:00", "Z")


def _reply(number: int, minutes_ago: int = 1) -> str:
    usage = {"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 5}
    record = {
        "type": "assistant",
        "sessionId": "s1",
        "requestId": f"r{number}",
        "timestamp": _stamp(minutes_ago),
        "cwd": "/work/project",
        "message": {"id": f"m{number}", "model": "claude-opus-5-5", "usage": usage},
    }

    return json.dumps(record) + "\n"


def _codex_line(kind: str, payload: dict[str, Any], minutes_ago: int = 1) -> str:
    return json.dumps({"timestamp": _stamp(minutes_ago), "type": kind, "payload": payload}) + "\n"


def _token_count(total: int, used: float) -> str:
    rate = {"primary": {"used_percent": used, "window_minutes": 300}}
    info = {"total_token_usage": {"input_tokens": total, "cached_input_tokens": 0, "output_tokens": total // 10}}

    return _codex_line("event_msg", {"type": "token_count", "info": info, "rate_limits": rate})


class SharedReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home_folder = tempfile.TemporaryDirectory()
        self._home = self._home_folder.name
        self._shared = os.path.join(self._home, "shared")
        self._projects = os.path.join(self._home, ".claude", "projects", "p")

        os.makedirs(self._projects)

    def tearDown(self) -> None:
        self._home_folder.cleanup()

    def _run_reader(
        self,
        script: str,
        name: str,
        since: str,
        arguments: tuple[str, ...] = (),
        key: str = "",
    ) -> subprocess.CompletedProcess[str]:
        path = os.path.join(self._shared, f"{name}.json")
        argv = [PYTHON, "-I", "-c", script, path, key, since, LIMIT_SECONDS, *arguments]

        return subprocess.run(argv, env={"HOME": self._home}, capture_output=True, text=True, check=False)

    def _count(self, since: str = NOW_ONLY) -> dict[str, Any]:
        ran = self._run_reader(HISTORY, "history", since)

        self.assertEqual(ran.returncode, 0, ran.stderr)
        output: dict[str, Any] = json.loads(ran.stdout)["output"]

        output.pop("generatedAt")
        return output

    def _recount(self) -> dict[str, Any]:
        os.remove(os.path.join(self._shared, "history.scan.json"))
        return self._count()

    def _append(self, name: str, text: str) -> None:
        with open(os.path.join(self._projects, name), "a", encoding="utf-8") as file:
            file.write(text)

    def _read_codex(self) -> dict[str, Any]:
        ran = self._run_reader(CODEX, "codex-local", NOW_ONLY, LOCAL)

        self.assertEqual(ran.returncode, 0, ran.stderr)
        output: dict[str, Any] = json.loads(ran.stdout)["output"]

        return output

    def test_the_busy_code_is_the_one_the_mod_waits_for(self) -> None:
        self.assertIn(f"\nBUSY = {BUSY}\n", SHARED_READER)

    def test_a_count_read_on_equals_a_count_from_the_start(self) -> None:
        self._append("a.jsonl", _reply(1) + _reply(2))
        first = self._count()

        self._append("a.jsonl", _reply(3) + _reply(1))
        self._append("b.jsonl", _reply(4))
        read_on = self._count()

        self.assertEqual(first["days"][0]["replies"], 2)
        self.assertEqual(read_on["days"][0]["replies"], 4)
        self.assertEqual(read_on, self._recount())

    def test_a_reply_older_than_the_week_is_not_kept(self) -> None:
        self._append("a.jsonl", _reply(1, 8 * 24 * 60) + _reply(2))
        self._count()
        with open(os.path.join(self._shared, "history.scan.json"), encoding="utf-8") as file:
            kept = json.load(file)

        self.assertEqual([entry[0] for scan in kept.values() for entry in scan["entries"]], ["m2"])

    def test_a_line_still_being_written_counts_once_it_is_whole(self) -> None:
        whole = _reply(2)

        self._append("a.jsonl", _reply(1) + whole[:40])
        before = self._count()

        self._append("a.jsonl", whole[40:])
        self.assertEqual(before["days"][0]["replies"], 1)
        self.assertEqual(self._count()["days"][0]["replies"], 2)

    def test_a_transcript_written_anew_is_read_from_its_start(self) -> None:
        self._append("a.jsonl", _reply(1) + _reply(2) + _reply(3))
        self._count()
        with open(os.path.join(self._projects, "a.jsonl"), "w", encoding="utf-8") as file:
            file.write(_reply(7))
        self.assertEqual(self._count()["days"][0]["replies"], 1)

    def test_a_reading_made_since_the_time_asked_is_taken_as_it_is(self) -> None:
        self._append("a.jsonl", _reply(1))
        made = json.loads(self._run_reader(HISTORY, "history", EVER).stdout)

        self._append("a.jsonl", _reply(2))
        again = self._run_reader(HISTORY, "history", str(made["at"] - 1))

        self.assertEqual(json.loads(again.stdout), made)

    def test_a_reader_exits_busy_while_another_holds_the_lock(self) -> None:
        os.makedirs(self._shared)
        with open(os.path.join(self._shared, "history.json.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ran = self._run_reader(HISTORY, "history", EVER)

        self.assertEqual((ran.returncode, ran.stdout), (BUSY, ""))

    def test_a_read_past_its_limit_stops_and_lets_the_lock_go(self) -> None:
        slow = f"{SHARED_READER}\n\ndef read_slowly(arguments, kept):\n    time.sleep(5)\n\n\nrun_shared(read_slowly)\n"
        path = os.path.join(self._shared, "slow.json")
        started = time.monotonic()
        ran = subprocess.run([PYTHON, "-I", "-c", slow, path, "", EVER, "1"], capture_output=True, text=True)

        self.assertNotEqual(ran.returncode, 0)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual([name for name in os.listdir(self._shared) if name.endswith(".tmp")], [])
        with open(f"{path}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_a_codex_record_read_on_equals_one_read_from_the_start(self) -> None:
        folder = os.path.join(self._home, ".codex", "sessions", datetime.date.today().strftime("%Y/%m/%d"))
        record = os.path.join(folder, "rollout-a.jsonl")
        head = {"originator": "codex_exec", "source": "exec", "timestamp": _stamp(10), "cwd": "/work"}

        os.makedirs(folder)
        with open(record, "w", encoding="utf-8") as file:
            file.write(_codex_line("session_meta", head, 10))
            file.write(_codex_line("turn_context", {"model": "gpt-6-astra", "effort": "high"}))
            file.write(_token_count(1000, 10.0))
        self._read_codex()
        with open(record, "a", encoding="utf-8") as file:
            file.write(_token_count(5000, 14.0))
            file.write(_codex_line("event_msg", {"type": "task_complete"}))
        read_on = self._read_codex()

        os.remove(os.path.join(self._shared, "codex-local.scan.json"))
        from_start = self._read_codex()

        self.assertEqual(read_on["last"]["read"], 5000)
        self.assertEqual(read_on["last"]["rise"], 4.0)
        self.assertEqual(read_on["last"]["model"], "gpt-6-astra")
        self.assertEqual(read_on, from_start)


if __name__ == "__main__":
    unittest.main()
