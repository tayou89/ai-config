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
FAKE_LSOF = """while IFS= read -r lsof_line; do
    printf '%s\\n' "$lsof_line"
done < "$HOME/lsof.stdout"
while IFS= read -r lsof_line; do
    printf '%s\\n' "$lsof_line" >&2
done < "$HOME/lsof.stderr"
IFS= read -r lsof_status < "$HOME/lsof.status"
exit "$lsof_status"
"""
# Codex sends its own instructions as a user message before the brief, and
# they may quote the reservation line without starting with it.
CODEX_INSTRUCTIONS = {
    "type": "message",
    "role": "user",
    "content": [{"type": "input_text", "text": "# AGENTS.md instructions\nBriefs start with Budget reservation: <id>"}],
}


def _read_register() -> str:
    with open(REGISTER, encoding="utf-8") as file:
        return file.read()


def _script(source: str, name: str) -> str:
    found = re.search(rf"^const {name} = String\.raw`\n(.*?)^`", source, re.S | re.M)

    if found is None:
        raise LookupError(f"{name} is not in register.tsx")
    else:
        return found.group(1)


def _shared_name(source: str, key: str) -> str:
    found = re.search(rf"^  {key}: '([^']+)',$", source, re.M)

    if found is None:
        raise LookupError(f"SHARED_NAME.{key} is not in register.tsx")
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
CODEX_LOCAL_NAME = _shared_name(SOURCE, "codexLocal")
# The name the band read Codex under before older bands had to be kept apart.
OLDER_CODEX_LOCAL_NAME = "codex-local"


def _stamp(minutes_ago: int) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)

    return moment.isoformat().replace("+00:00", "Z")


def _ms(stamp: str) -> int:
    return int(datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


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
        self._set_open()

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

        if script == CODEX:
            argv.extend(["/bin/sh", "-c", FAKE_LSOF])
        return subprocess.run(argv, env={"HOME": self._home}, capture_output=True, text=True, check=False)

    def _set_open(self, *paths: str, code: int = 0, error: str = "") -> None:
        stdout = "".join(f"p{100 + index}\nn{path}\n" for index, path in enumerate(paths))

        for name, text in (("stdout", stdout), ("stderr", error + "\n" if error else ""), ("status", f"{code}\n")):
            with open(os.path.join(self._home, f"lsof.{name}"), "w", encoding="utf-8") as file:
                file.write(text)

    def _record(self, name: str, reservation: str | None = "reservation-a", stamp: str | None = None) -> str:
        folder = os.path.join(self._home, ".codex", "sessions", datetime.date.today().strftime("%Y/%m/%d"))
        path = os.path.join(folder, f"rollout-{name}.jsonl")
        head = {"originator": "codex_exec", "source": "exec", "timestamp": stamp or _stamp(10), "cwd": "/work"}
        text = f"Budget reservation: {reservation}\nDo the work" if reservation else "Do the work"
        message = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}

        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write(_codex_line("session_meta", head))
            file.write(_codex_line("response_item", CODEX_INSTRUCTIONS))
            file.write(_codex_line("response_item", message))
            file.write(_codex_line("event_msg", {"type": "task_started", "turn_id": "turn-1"}))
            file.write(_codex_line("turn_context", {"model": "gpt-6-astra", "effort": "high"}))
            file.write(_token_count(1000, 10))
        return path

    def _ledger(self, **owners: str) -> None:
        folder = os.path.join(self._home, ".claude", "codex-budget")

        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "ledger.jsonl"), "w", encoding="utf-8") as file:
            for identifier, session in owners.items():
                file.write(json.dumps({"event": "reserve", "identifier": identifier, "session": session}) + "\n")

    def _event(self, path: str, kind: str, turn: str = "turn-1") -> int:
        line = _codex_line("event_msg", {"type": kind, "turn_attribution": {"turn_id": turn}})

        with open(path, "a", encoding="utf-8") as file:
            file.write(line)
        return _ms(json.loads(line)["timestamp"])

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
        ran = self._run_reader(CODEX, CODEX_LOCAL_NAME, NOW_ONLY, LOCAL)

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
            file.write(_codex_line("event_msg", {"type": "task_started"}))
            file.write(_codex_line("turn_context", {"model": "gpt-6-astra", "effort": "high"}))
            file.write(_token_count(1000, 10.0))
        self._read_codex()
        with open(record, "a", encoding="utf-8") as file:
            file.write(_token_count(5000, 14.0))
            file.write(_codex_line("event_msg", {"type": "task_complete"}))
        read_on = self._read_codex()

        os.remove(os.path.join(self._shared, f"{CODEX_LOCAL_NAME}.scan.json"))
        from_start = self._read_codex()

        self.assertEqual(read_on["last"]["read"], 5000)
        self.assertEqual(read_on["last"]["rise"], 4.0)
        self.assertEqual(read_on["last"]["model"], "gpt-6-astra")
        self.assertEqual(read_on, from_start)

    def test_finished_codex_runs_come_with_when_they_started(self) -> None:
        folder = os.path.join(self._home, ".codex", "sessions", datetime.date.today().strftime("%Y/%m/%d"))
        head = {"originator": "codex_exec", "source": "exec", "timestamp": _stamp(10), "cwd": "/work"}

        os.makedirs(folder)
        with open(os.path.join(folder, "rollout-done.jsonl"), "w", encoding="utf-8") as file:
            file.write(_codex_line("session_meta", head, 10))
            file.write(_codex_line("event_msg", {"type": "task_started"}))
            file.write(_token_count(1000, 10.0))
            file.write(_token_count(3000, 12.0))
            file.write(_codex_line("event_msg", {"type": "task_complete"}))
        with open(os.path.join(folder, "rollout-open.jsonl"), "w", encoding="utf-8") as file:
            file.write(_codex_line("session_meta", {**head, "timestamp": _stamp(2)}, 2))
            file.write(_token_count(500, 12.0))
        finished = self._read_codex()["finished"]

        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["startedAt"], _ms(head["timestamp"]))
        self.assertEqual(finished[0]["steps"], 2)
        self.assertEqual(finished[0]["read"], 3000)
        self.assertEqual(finished[0]["rise"], 2.0)
        self.assertGreaterEqual(finished[0]["finishedAt"], finished[0]["startedAt"])

    def test_same_millisecond_records_remain_two_owned_running_runs(self) -> None:
        stamp = _stamp(5)
        first = self._record("one", stamp=stamp)
        second = self._record("two", stamp=stamp)

        self._ledger(**{"reservation-a": "session-a"})
        self._set_open(first, second)
        output = self._read_codex()

        self.assertEqual(output["running"], 2)
        self.assertEqual(len(output["records"]), 2)
        ids = {run["recordId"] for run in output["records"]}
        is_owned_running = all(
            run["state"] == "running" and run["ownerSessionId"] == "session-a" for run in output["records"]
        )

        self.assertEqual(ids, {os.path.basename(first), os.path.basename(second)})
        self.assertTrue(is_owned_running)

    def test_reservation_ledger_determines_the_owner_including_other_sessions(self) -> None:
        first = self._record("one", "reservation-a")
        second = self._record("two", "reservation-b")

        self._ledger(**{"reservation-a": "session-a", "reservation-b": "session-b"})
        self._set_open(first, second)
        runs = {run["reservation"]: run for run in self._read_codex()["records"]}

        self.assertEqual(runs["reservation-a"]["ownerSessionId"], "session-a")
        self.assertEqual(runs["reservation-b"]["ownerSessionId"], "session-b")
        self._ledger(**{"reservation-a": "session-a", "reservation-b": "session-c"})
        updated = {run["reservation"]: run for run in self._read_codex()["records"]}

        self.assertEqual(updated["reservation-b"]["ownerSessionId"], "session-c")

    def test_missing_or_unlisted_reservation_has_unknown_owner(self) -> None:
        first = self._record("none", None)
        second = self._record("unlisted", "missing")

        self._ledger(known="session-a")
        self._set_open(first, second)
        self.assertTrue(all(run["ownerSessionId"] == "unknown" for run in self._read_codex()["records"]))

    def test_a_closed_old_record_that_grows_is_read_again(self) -> None:
        path = self._record("old", stamp=_stamp(180))
        name = os.path.basename(path)
        long_ago = time.time() - 3 * 60 * 60

        self._event(path, "task_complete")
        self._ledger(**{"reservation-a": "session-a"})
        self._set_open()
        first = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(first[name]["state"], "done")
        self._event(path, "task_started", turn="turn-2")
        os.utime(path, (long_ago, long_ago))
        runs = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(runs[name]["state"], "stopped")

    def test_the_band_reads_codex_under_a_name_older_bands_do_not_use(self) -> None:
        self.assertNotEqual(CODEX_LOCAL_NAME, OLDER_CODEX_LOCAL_NAME)

    def test_a_scan_an_older_band_left_without_the_reservation_stays_under_its_own_name(self) -> None:
        path = self._record("mixed", "reservation-a")
        scan_path = os.path.join(self._shared, f"{OLDER_CODEX_LOCAL_NAME}.scan.json")

        self._ledger(**{"reservation-a": "session-a"})
        self._set_open(path)
        older = self._run_reader(CODEX, OLDER_CODEX_LOCAL_NAME, NOW_ONLY, LOCAL)

        self.assertEqual(older.returncode, 0, older.stderr)
        with open(scan_path, encoding="utf-8") as file:
            scans = json.load(file)
        # An older band carries the newer fields over, reservation unread, and moves the place to the end.
        scans[path] = {**scans[path], "reservation": None, "offset": os.path.getsize(path)}
        with open(scan_path, "w", encoding="utf-8") as file:
            json.dump(scans, file)
        poisoned = self._run_reader(CODEX, OLDER_CODEX_LOCAL_NAME, NOW_ONLY, LOCAL)
        owner = json.loads(poisoned.stdout)["output"]["records"][0]["ownerSessionId"]

        self.assertEqual(owner, "unknown")
        self.assertEqual(self._read_codex()["records"][0]["ownerSessionId"], "session-a")

    def test_a_lock_on_the_older_name_does_not_hold_up_the_newer_one(self) -> None:
        self._record("locked")
        self._set_open()
        os.makedirs(self._shared, exist_ok=True)
        with open(os.path.join(self._shared, f"{OLDER_CODEX_LOCAL_NAME}.json.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ran = self._run_reader(CODEX, CODEX_LOCAL_NAME, NOW_ONLY, LOCAL)

        self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_the_reservation_after_codex_own_instructions_names_the_owner(self) -> None:
        path = self._record("instructed", "reservation-a")

        self._ledger(**{"reservation-a": "session-a"})
        self._set_open(path)
        run = self._read_codex()["records"][0]

        self.assertEqual(run["reservation"], "reservation-a")
        self.assertEqual(run["ownerSessionId"], "session-a")

    def test_open_complete_incomplete_and_aborted_records_have_distinct_states(self) -> None:
        running = self._record("running")
        done = self._record("done")
        stopped = self._record("stopped")
        aborted = self._record("aborted")
        ended = self._event(done, "task_complete")

        self._event(running, "task_complete")
        self._event(aborted, "turn_aborted")
        self._event(aborted, "task_complete")
        self._set_open(running)
        runs = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(runs[os.path.basename(running)]["state"], "running")
        self.assertEqual(runs[os.path.basename(done)]["state"], "done")
        self.assertEqual(runs[os.path.basename(done)]["finishedAt"], ended)
        self.assertEqual(runs[os.path.basename(stopped)]["state"], "stopped")
        self.assertEqual(runs[os.path.basename(aborted)]["state"], "stopped")
        self.assertNotIn("finishedAt", runs[os.path.basename(running)])

    def test_an_old_complete_does_not_end_a_resumed_turn(self) -> None:
        path = self._record("resumed")

        self._event(path, "task_complete")
        self._read_codex()
        self._event(path, "task_started", "turn-2")
        self._event(path, "task_complete", "turn-1")
        self.assertEqual(self._read_codex()["records"][0]["state"], "stopped")
        self._set_open(path)
        self.assertEqual(self._read_codex()["records"][0]["state"], "running")

    def test_lsof_failure_marks_unfinished_runs_unknown_and_never_done(self) -> None:
        path = self._record("running")
        done = self._record("complete")

        self._event(done, "task_complete")
        self._set_open(path)
        self._read_codex()
        for code, error in [(2, "failed"), (1, "permission denied")]:
            self._set_open(code=code, error=error)
            output = self._read_codex()

            self.assertTrue(output["isProcessReadFailed"])
            self.assertTrue(all(run["state"] == "unknown" for run in output["records"]))
            self.assertEqual(output["finished"], [])
        self._set_open(code=1)
        self.assertFalse(self._read_codex()["isProcessReadFailed"])
        missing = self._run_reader(CODEX, CODEX_LOCAL_NAME, NOW_ONLY, (*LOCAL, "/missing-test-lsof"))
        failed = json.loads(missing.stdout)["output"]

        self.assertTrue(failed["isProcessReadFailed"])
        self.assertTrue(all(run["state"] == "unknown" for run in failed["records"]))

    def test_search_and_script_runs_count_only_by_their_open_files(self) -> None:
        search = self._record("search")
        script = self._record("run-review")
        child = self._record("approval-child")

        with open(child, encoding="utf-8") as file:
            content = file.read().replace('"source": "exec"', '"source": {"subagent": "review"}')
        with open(child, "w", encoding="utf-8") as file:
            file.write(content)
        self._set_open(search, script, child)
        output = self._read_codex()

        self.assertEqual(output["running"], 2)
        self.assertEqual(len(output["working"]), 2)

    def test_kept_owner_and_turn_scans_equal_a_full_rescan_after_resume(self) -> None:
        path = self._record("resumed")

        self._ledger(**{"reservation-a": "session-a"})
        self._event(path, "task_complete")
        self._read_codex()
        self._event(path, "task_started", "turn-2")
        self._event(path, "task_complete", "turn-1")
        self._set_open(path)
        read_on = self._read_codex()

        os.remove(os.path.join(self._shared, f"{CODEX_LOCAL_NAME}.scan.json"))
        self.assertEqual(read_on, self._read_codex())

    def test_old_closed_records_are_not_scanned_but_open_and_kept_unknown_records_are(self) -> None:
        old = self._record("old-closed", stamp=_stamp(3 * 60))
        running = self._record("old-running", stamp=_stamp(3 * 60))
        recent = self._record("recent")
        long_ago = time.time() - 3 * 60 * 60

        # A scan would fail here; neither the first line nor the limits tail needs it.
        with open(old, "a", encoding="utf-8") as file:
            file.write('{"type":"event_msg","payload":{"type":"task_started"}\n')
        os.utime(old, (long_ago, long_ago))
        self._set_open(running)
        output = self._read_codex()

        self.assertEqual(
            {run["recordId"] for run in output["records"]},
            {os.path.basename(running), os.path.basename(recent)},
        )
        self._set_open(code=2, error="failed")
        unknown = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(unknown[os.path.basename(running)]["state"], "unknown")
        self._event(running, "task_complete")
        self._set_open()
        ended = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(ended[os.path.basename(running)]["state"], "done")
        # Idle sessions must still see the final state after another reader saw it.
        cached = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(cached[os.path.basename(running)]["state"], "done")
        # A record that grows after it closed was resumed: its new turn counts.
        self._event(running, "task_started", "turn-2")
        resumed = {run["recordId"]: run for run in self._read_codex()["records"]}

        self.assertEqual(resumed[os.path.basename(running)]["state"], "stopped")

    def test_irrelevant_lines_are_not_decoded_and_later_user_messages_are_not_read(self) -> None:
        path = self._record("filtered")

        with open(path, "a", encoding="utf-8") as file:
            file.write('{"type":"response_item","payload":{"role":"user"}\n')
            file.write('{"type":"event_msg","payload":{"type":"irrelevant"}\n')
        self._event(path, "task_complete")
        self.assertEqual(self._read_codex()["records"][0]["state"], "done")


if __name__ == "__main__":
    unittest.main()
