#!/usr/bin/env python3
"""Tests for scripts/folder_owner.py: one owner session per task worktree, claimed and handed over atomically."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from owner_world import OTHER, OWNER, TERMINAL, THIRD, OwnerWorld, Session
from workflow_world import ENCODING, SCRIPTS_DIRECTORY, load_script

folder_owner = load_script("folder_owner")
CONTENDERS = 8
CLAIM_ROUNDS = 12
CONTENDER_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import folder_owner
    worktree = folder_owner.find_linked_worktree(Path(sys.argv[2]))
    me = folder_owner.SessionIdentity(None, sys.argv[3], None)
    barrier = Path(sys.argv[5])
    expected = folder_owner.read_owner(worktree)
    (barrier / sys.argv[3]).touch()
    while not (barrier / "go").exists():
        time.sleep(0.005)
    if sys.argv[4] == "claim":
        owner = folder_owner.claim_folder(worktree, me, "", folder_owner.ClaimMethod.FIRST_WRITE)
    else:
        owner = folder_owner.transfer_folder(worktree, expected, me, "", folder_owner.ClaimMethod.TAKEN_OVER)
    print(owner.owner.cli_session)
    """
)


def identity(session: Session) -> folder_owner.SessionIdentity:
    return folder_owner.SessionIdentity(session.app, session.cli, None)


class FolderOwnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree_path = self.world.add_worktree("task-a")
        self.worktree = folder_owner.find_linked_worktree(self.worktree_path)
        home = patch.dict(os.environ, {"HOME": str(self.world.home)})

        home.start()
        self.addCleanup(home.stop)

    def test_finds_linked_worktrees_only(self) -> None:
        nested = self.worktree_path / "deep" / "new-file.txt"
        submodule = self.world.root / "submodule"
        submodule_git = self.world.root / "modules" / "submodule"

        submodule_git.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "--separate-git-dir", str(submodule_git), str(submodule)],
            env=self.world.environment(OWNER),
            capture_output=True,
            check=True,
        )
        self.assertEqual(self.worktree.root, self.worktree_path)
        self.assertEqual(self.worktree.git_directory, self.world.git_directory(self.worktree_path))
        self.assertEqual(folder_owner.find_linked_worktree(nested), self.worktree)
        self.assertIsNone(folder_owner.find_linked_worktree(self.world.original / "README.md"))
        self.assertIsNone(folder_owner.find_linked_worktree(submodule))
        self.assertIsNone(folder_owner.find_linked_worktree(self.world.root))

    def test_claim_records_the_first_owner_and_keeps_it(self) -> None:
        first = folder_owner.claim_folder(self.worktree, identity(OWNER), OWNER.title, folder_owner.ClaimMethod.CREATED)
        second = folder_owner.claim_folder(self.worktree, identity(OTHER), OTHER.title, folder_owner.ClaimMethod.CREATED)

        self.assertTrue(first.owner.is_same(identity(OWNER)))
        self.assertEqual(second, first)
        self.assertEqual(folder_owner.read_owner(self.worktree), first)
        self.assertEqual(self.world.read_owner(self.worktree_path)["title"], OWNER.title)

    def test_concurrent_claims_leave_exactly_one_owner(self) -> None:
        for round_number in range(CLAIM_ROUNDS):
            with self.subTest(round=round_number):
                winners = self.run_contenders("claim")

                self.assertEqual(len(set(winners)), 1, winners)
                self.assertEqual(self.world.read_owner_cli(self.worktree_path), winners[0])
                self.world.owner_file(self.worktree_path).unlink()

    def test_concurrent_transfers_from_one_owner_have_one_winner(self) -> None:
        for round_number in range(CLAIM_ROUNDS // 2):
            with self.subTest(round=round_number):
                self.world.write_owner(self.worktree_path, OWNER)
                winners = self.run_contenders("transfer")

                self.assertEqual(len(set(winners)), 1, winners)
                self.assertEqual(self.world.read_owner_cli(self.worktree_path), winners[0])

    def test_transfer_from_a_stale_owner_returns_the_current_one(self) -> None:
        self.world.write_owner(self.worktree_path, OWNER)
        stale = folder_owner.read_owner(self.worktree)
        current = folder_owner.transfer_folder(
            self.worktree, stale, identity(OTHER), OTHER.title, folder_owner.ClaimMethod.HANDED_OVER
        )
        result = folder_owner.transfer_folder(
            self.worktree, stale, identity(THIRD), THIRD.title, folder_owner.ClaimMethod.TAKEN_OVER
        )

        self.assertTrue(current.owner.is_same(identity(OTHER)))
        self.assertEqual(current.previous, stale.owner)
        self.assertEqual(result, current)

    def test_lock_of_a_process_that_died_holding_it_does_not_block(self) -> None:
        self.world.write_owner(self.worktree_path, OWNER)
        holder = self.start_lock_holder()

        holder.kill()
        holder.wait()
        expected = folder_owner.read_owner(self.worktree)
        result = folder_owner.transfer_folder(
            self.worktree, expected, identity(OTHER), OTHER.title, folder_owner.ClaimMethod.TAKEN_OVER
        )

        self.assertTrue(result.owner.is_same(identity(OTHER)))

    def test_lock_held_by_a_live_process_refuses_after_waiting(self) -> None:
        self.world.write_owner(self.worktree_path, OWNER)
        holder = self.start_lock_holder()

        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        expected = folder_owner.read_owner(self.worktree)

        with self.assertRaises(folder_owner.OwnerBusyError):
            folder_owner.transfer_folder(
                self.worktree, expected, identity(OTHER), OTHER.title, folder_owner.ClaimMethod.TAKEN_OVER
            )
        self.assertEqual(self.world.read_owner_cli(self.worktree_path), OWNER.cli)

    def start_lock_holder(self) -> subprocess.Popen[str]:
        """A process that takes the owner lock, says so, and keeps it until it is killed."""
        lock_file = self.worktree.git_directory / folder_owner.LOCK_FILE_NAME
        code = (
            "import fcntl, sys, time\n"
            "handle = open(sys.argv[1], 'a')\n"
            "fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
            "print('locked', flush=True)\n"
            "time.sleep(60)\n"
        )
        holder = subprocess.Popen([sys.executable, "-c", code, str(lock_file)], stdout=subprocess.PIPE, text=True)

        self.addCleanup(holder.stdout.close)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        return holder

    def test_same_app_session_with_a_new_claude_code_id_is_the_same_owner(self) -> None:
        resumed = folder_owner.SessionIdentity(OWNER.app, "cli-owner-resumed", None)
        terminal = identity(TERMINAL)

        self.assertTrue(identity(OWNER).is_same(resumed))
        self.assertFalse(identity(OWNER).is_same(identity(OTHER)))
        self.assertTrue(terminal.is_same(folder_owner.SessionIdentity(None, TERMINAL.cli, None)))
        self.assertFalse(terminal.is_same(folder_owner.SessionIdentity(None, "cli-terminal-other", None)))
        self.assertFalse(terminal.is_same(folder_owner.SessionIdentity(OTHER.app, TERMINAL.cli + "x", None)))

    def test_current_identity_prefers_the_app_session(self) -> None:
        app_environment = {"CLAUDE_CODE_HOST_SESSION_ID": OWNER.app, "CLAUDE_CODE_SESSION_ID": OWNER.cli}

        with patch.dict(os.environ, app_environment):
            current = folder_owner.read_current_identity()
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": ""}):
            os.environ.pop("CLAUDE_CODE_HOST_SESSION_ID", None)
            from_payload = folder_owner.read_current_identity(TERMINAL.cli)
            nobody = folder_owner.read_current_identity()
        self.assertEqual((current.app_session, current.cli_session), (OWNER.app, OWNER.cli))
        self.assertEqual((from_payload.app_session, from_payload.cli_session), (None, TERMINAL.cli))
        self.assertIsNone(nobody)

    def test_owner_state_follows_the_app_record_and_the_process(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])

        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        live_mark = folder_owner.read_process_mark(sleeper.pid)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])

        dead.wait()
        cases = [
            (OWNER, None, False, folder_owner.OwnerState.ACTIVE),
            (OWNER, None, True, folder_owner.OwnerState.GONE),
            (Session("local_missing", "cli-missing", ""), None, False, folder_owner.OwnerState.UNKNOWN),
            (TERMINAL, live_mark, False, folder_owner.OwnerState.ACTIVE),
            (TERMINAL, folder_owner.ProcessMark(dead.pid, "Thu Jan  1 00:00:00 1970"), False, folder_owner.OwnerState.GONE),
            (TERMINAL, None, False, folder_owner.OwnerState.UNKNOWN),
        ]

        self.assertIsNotNone(live_mark)
        for session, mark, archived, expected in cases:
            with self.subTest(session=session.cli, mark=mark, archived=archived):
                self.world.write_app_record(OWNER, isArchived=archived)
                owner = folder_owner.FolderOwner(
                    folder_owner.SessionIdentity(session.app, session.cli, mark),
                    session.title,
                    1.0,
                    folder_owner.ClaimMethod.CREATED,
                    None,
                )

                self.assertEqual(folder_owner.read_owner_state(owner), expected)

    def test_a_process_lookup_that_fails_is_unknown_not_gone(self) -> None:
        mark = folder_owner.ProcessMark(os.getpid(), "Thu Jan  1 00:00:00 1970")
        owner = folder_owner.FolderOwner(
            folder_owner.SessionIdentity(None, TERMINAL.cli, mark),
            "",
            1.0,
            folder_owner.ClaimMethod.CREATED,
            None,
        )
        timeout = subprocess.TimeoutExpired("ps", 3)

        with patch.object(folder_owner.subprocess, "run", side_effect=timeout):
            self.assertEqual(folder_owner.read_owner_state(owner), folder_owner.OwnerState.UNKNOWN)

    def test_refreshing_the_owners_process_keeps_the_rest_of_the_record(self) -> None:
        handed = folder_owner.FolderOwner(
            folder_owner.SessionIdentity(None, TERMINAL.cli, None),
            "",
            5.0,
            folder_owner.ClaimMethod.HANDED_OVER,
            identity(OWNER),
        )
        mark = folder_owner.ProcessMark(os.getpid(), "Thu Jan  1 00:00:00 1970")
        acting = folder_owner.SessionIdentity(None, TERMINAL.cli, mark)

        with folder_owner._write_temporary_record(self.worktree, handed) as temporary:
            os.replace(temporary, self.worktree.owner_file)
        folder_owner.refresh_owner_process(self.worktree, handed, acting)
        refreshed = folder_owner.read_owner(self.worktree)

        self.assertEqual(refreshed.owner.process, mark)
        self.assertEqual((refreshed.previous, refreshed.claimed_at), (identity(OWNER), 5.0))

    def test_app_worktree_names_the_session_the_app_made_it_for(self) -> None:
        self.world.write_app_record(OTHER, worktreePath=str(self.worktree_path))
        self.assertTrue(folder_owner.is_app_worktree(self.worktree, identity(OTHER)))
        self.assertFalse(folder_owner.is_app_worktree(self.worktree, identity(OWNER)))
        self.assertFalse(folder_owner.is_app_worktree(self.worktree, identity(TERMINAL)))

    def test_unreadable_owner_file_is_an_error(self) -> None:
        self.world.owner_file(self.worktree_path).write_text("{not json", encoding=ENCODING)
        with self.assertRaises(folder_owner.OwnerFileError):
            folder_owner.read_owner(self.worktree)

    def test_gaps_are_kept_until_settled(self) -> None:
        folder_owner.add_gap(self.worktree, identity(OTHER), "Bash: ValueError: broken", None)
        folder_owner.add_gap(self.worktree, None, "Edit: OSError: gone", None)
        gaps = folder_owner.read_gaps(self.worktree)

        self.assertEqual([gap.what for gap in gaps], ["Bash: ValueError: broken", "Edit: OSError: gone"])
        self.assertEqual(gaps[0].session, OTHER.app)
        folder_owner.settle_gaps(self.worktree, gaps)
        self.assertEqual(folder_owner.read_gaps(self.worktree), [])

    def test_a_gap_added_after_the_commit_read_them_stays(self) -> None:
        folder_owner.add_gap(self.worktree, identity(OTHER), "Bash: ValueError: seen", None)
        shown = folder_owner.read_gaps(self.worktree)

        folder_owner.add_gap(self.worktree, identity(THIRD), "Bash: ValueError: later", None)
        folder_owner.settle_gaps(self.worktree, shown)
        left = folder_owner.read_gaps(self.worktree)
        settled = (self.worktree.git_directory / "claude-owner-gaps-settled.jsonl").read_text(encoding=ENCODING)

        self.assertEqual([gap.what for gap in left], ["Bash: ValueError: later"])
        self.assertIn("seen", settled)

    def test_one_call_is_one_gap_however_often_the_guard_fails_on_it(self) -> None:
        folder_owner.add_gap(self.worktree, identity(OTHER), "Bash: OSError: before the call", "toolu_a")
        folder_owner.add_gap(self.worktree, identity(OTHER), "Bash: OSError: after the call", "toolu_a")
        folder_owner.add_gap(self.worktree, identity(OTHER), "Bash: OSError: another call", "toolu_b")
        folder_owner.add_gap(self.worktree, None, "Bash: OSError: no call id", None)
        folder_owner.add_gap(self.worktree, None, "Bash: OSError: no call id again", None)
        gaps = folder_owner.read_gaps(self.worktree)

        self.assertEqual([gap.call_id for gap in gaps], ["toolu_a", "toolu_b", None, None])

    def test_an_unreadable_gap_record_raises_rather_than_reads_as_one_gap(self) -> None:
        """A dialog could not show what such a record holds, so its approval must not cover it (user decision,
        2026-11-24)."""
        self.worktree.gaps_file.write_bytes(b"\xff\xfe not text")
        with self.assertRaises(folder_owner.OwnerFileError):
            folder_owner.read_gaps(self.worktree)

    def test_uncommitted_changes_include_untracked_files(self) -> None:
        self.assertFalse(folder_owner.has_uncommitted_changes(self.worktree))
        self.world.write(self.worktree_path, "new.txt", "x\n")
        self.assertTrue(folder_owner.has_uncommitted_changes(self.worktree))

    def test_owner_file_names_are_protected(self) -> None:
        owner_file = self.world.owner_file(self.worktree_path)

        self.assertTrue(folder_owner.is_protected_file(owner_file))
        self.assertTrue(folder_owner.is_protected_file(owner_file.with_name(folder_owner.LOCK_FILE_NAME)))
        self.assertTrue(folder_owner.is_protected_file(owner_file.with_name(folder_owner.GAPS_FILE_NAME)))
        self.assertFalse(folder_owner.is_protected_file(self.worktree_path / folder_owner.OWNER_FILE_NAME))

    def run_contenders(self, action: str) -> list[str]:
        """Start the contenders, let each read the owner, then release them all at once."""
        barrier = Path(tempfile.mkdtemp(dir=self.world.root))
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    CONTENDER_SCRIPT,
                    str(SCRIPTS_DIRECTORY),
                    str(self.worktree_path),
                    f"cli-contender-{number}",
                    action,
                    str(barrier),
                ],
                env={**os.environ, "HOME": str(self.world.home)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for number in range(CONTENDERS)
        ]

        while len(list(barrier.iterdir())) < CONTENDERS and all(process.poll() is None for process in processes):
            time.sleep(0.005)
        (barrier / "go").touch()
        outputs = [process.communicate(timeout=60) for process in processes]

        for (_, error), process in zip(outputs, processes):
            self.assertEqual(process.returncode, 0, error)
        return [output.strip() for output, _ in outputs]


class FolderOwnerCommandTest(unittest.TestCase):
    """The command line: show, claim, take, and hand-over."""

    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree = self.world.add_worktree("task-a")

    def test_owner_hands_the_folder_to_another_session(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        result = self.world.run_folder_owner(OWNER, self.worktree, "hand-over", "--to", OTHER.app)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)
        self.assertEqual(self.world.read_owner(self.worktree)["method"], "handed-over")

    def test_only_the_owner_hands_over(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        result = self.world.run_folder_owner(OTHER, self.worktree, "hand-over", "--to", OTHER.app)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OWNER.cli)

    def test_claim_takes_a_folder_without_an_owner_or_with_a_gone_one(self) -> None:
        first = self.world.run_folder_owner(OTHER, self.worktree, "claim")

        self.world.archive(OTHER)
        second = self.world.run_folder_owner(THIRD, self.worktree, "claim")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.world.read_owner_cli(self.worktree), THIRD.cli)
        self.assertEqual(self.world.read_owner(self.worktree)["previous"]["appSession"], OTHER.app)

    def test_claim_refuses_a_folder_whose_owner_is_open(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        result = self.world.run_folder_owner(OTHER, self.worktree, "claim")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("take --from", result.stderr)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OWNER.cli)

    def test_take_needs_the_current_owner(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        wrong = self.world.run_folder_owner(THIRD, self.worktree, "take", "--from", OTHER.app)
        right = self.world.run_folder_owner(THIRD, self.worktree, "take", "--from", OWNER.app)

        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(right.returncode, 0, right.stderr)
        self.assertEqual(self.world.read_owner_cli(self.worktree), THIRD.cli)

    def test_adopt_in_a_task_worktree_takes_the_folder(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIRECTORY / "session_writes.py"), "adopt", "--from", OWNER.app, "README.md"],
            cwd=self.worktree,
            env=self.world.environment(OTHER),
            capture_output=True,
            text=True,
            encoding=ENCODING,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)

    def test_show_names_the_owner(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        result = self.world.run_folder_owner(OTHER, self.worktree, "show")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(OWNER.title, result.stdout)
        self.assertIn(OWNER.app, result.stdout)

    def test_a_command_line_it_does_not_take_is_refused(self) -> None:
        cases = [("take",), ("hand-over", "--to"), ("give",), ("show", "a", "b")]

        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.world.run_folder_owner(OWNER, self.worktree, *arguments)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("usage", result.stderr)

    def test_outside_a_task_worktree_the_command_says_so(self) -> None:
        result = self.world.run_folder_owner(OWNER, self.world.original, "claim")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("task worktree", result.stderr)

    def test_owner_record_is_json_with_both_ids(self) -> None:
        self.world.run_folder_owner(OWNER, self.worktree, "claim")
        record = json.loads(self.world.owner_file(self.worktree).read_text(encoding=ENCODING))

        self.assertEqual(record["owner"]["appSession"], OWNER.app)
        self.assertEqual(record["owner"]["cliSession"], OWNER.cli)
        self.assertEqual(record["title"], OWNER.title)


if __name__ == "__main__":
    unittest.main()
