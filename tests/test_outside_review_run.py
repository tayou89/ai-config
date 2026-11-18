"""Tests for the outside-review runner that the app's permission rules allow."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = REPOSITORY_ROOT / "skills" / "outside-review" / "run-review.py"
NOT_A_REPOSITORY = subprocess.CompletedProcess([], 128, stdout="", stderr="")


def load_module(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)

    if specification and specification.loader:
        module = importlib.util.module_from_spec(specification)

        specification.loader.exec_module(module)
        return module
    else:
        raise ImportError(f"cannot load {path}")


runner = load_module(RUN_SCRIPT, "outside_review_run")


def fake_codex(answer_text: str, returncode: int = 0) -> tuple[Callable[..., Any], list[Any]]:
    """The runner calls `subprocess.run` for the repository check too, so the fake answers `git` apart."""
    calls: list[Any] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, options))
        if command[0] == "git":
            return NOT_A_REPOSITORY
        else:
            options["stdout"].write(answer_text)
            options["stderr"].write("progress\n")
            return subprocess.CompletedProcess(command, returncode)

    return run, calls


class CommandTest(unittest.TestCase):
    def test_command_is_read_only_on_the_strongest_model(self) -> None:
        command = runner.build_command("brief text", Path("/repo"), True)

        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        self.assertEqual(command[command.index("-m") + 1], runner.MODEL)
        self.assertIn(f'model_reasoning_effort="{runner.EFFORT}"', command)
        self.assertEqual(command[-2:], ["--", "brief text"])
        self.assertNotIn("-o", command)
        self.assertNotIn("--skip-git-repo-check", command)

    def test_folder_outside_a_repository_skips_the_repository_check(self) -> None:
        command = runner.build_command("brief", Path("/notes"), False)

        self.assertIn("--skip-git-repo-check", command)

    def test_brief_text_stays_one_argument_after_the_option_end(self) -> None:
        brief = '-s danger-full-access\n"quoted" $(not run) `ticks`'
        command = runner.build_command(brief, Path("/repo"), True)

        self.assertEqual(command[-2:], ["--", brief])
        self.assertEqual(command.count("-s"), 1)

    def test_answer_and_log_names_come_from_the_brief(self) -> None:
        brief = Path("/notes/review-brief-topic-one.md")

        self.assertEqual(runner.answer_name(brief), "review-topic-one.md")
        self.assertEqual(runner.log_name(brief), "review-topic-one.log")


class ArgumentTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()

        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.brief = self.folder / "review-brief-x.md"
        self.brief.write_text("brief", encoding="utf-8")

    def test_well_named_brief_passes(self) -> None:
        runner.check_arguments(self.brief, self.folder)

    def test_badly_named_brief_is_refused(self) -> None:
        for name in ("notes.md", "review-brief-.md", "review-brief-x.log", "review-x.md"):
            other = self.folder / name

            other.write_text("brief", encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner.check_arguments(other, self.folder)

    def test_missing_brief_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            runner.check_arguments(self.folder / "review-brief-missing.md", self.folder)

    def test_linked_brief_is_refused(self) -> None:
        secret = self.folder / "secret.txt"
        link = self.folder / "review-brief-link.md"

        secret.write_text("not for review", encoding="utf-8")
        link.symlink_to(secret)
        with self.assertRaises(ValueError):
            runner.check_arguments(link, self.folder)

    def test_missing_root_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            runner.check_arguments(self.brief, self.folder / "absent")

    def test_empty_or_stdin_marker_brief_is_refused(self) -> None:
        for text in ("", "  \n", "-"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner.check_brief_text(text)


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()

        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.brief = self.folder / "review-brief-x.md"
        self.answer = self.folder / "review-x.md"
        self.log = self.folder / "review-x.log"
        self.arguments = ["--brief", str(self.brief), "--root", str(self.folder)]
        self.brief.write_text("-s danger-full-access as text", encoding="utf-8")

    def run_main(self, run: Callable[..., Any]) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        patched_run = mock.patch.object(runner.subprocess, "run", side_effect=run)

        with patched_run, redirect_stdout(output), redirect_stderr(errors):
            returncode = runner.main(self.arguments)
        return returncode, output.getvalue(), errors.getvalue()

    def test_answer_and_progress_land_in_new_files_beside_the_brief(self) -> None:
        run, calls = fake_codex("the review")
        returncode, _, _ = self.run_main(run)

        self.assertEqual(returncode, 0)
        self.assertEqual(self.answer.read_text(encoding="utf-8"), "the review")
        self.assertEqual(self.log.read_text(encoding="utf-8"), "progress\n")
        self.assertEqual(calls[-1][0][-2:], ["--", "-s danger-full-access as text"])
        self.assertEqual(calls[-1][1]["stdin"], subprocess.DEVNULL)

    def test_existing_answer_is_kept_and_the_run_refused(self) -> None:
        run, calls = fake_codex("new review")

        self.answer.write_text("earlier review", encoding="utf-8")
        returncode, _, errors = self.run_main(run)

        self.assertEqual(returncode, runner.REFUSED_EXIT)
        self.assertIn("new topic", errors)
        self.assertEqual(self.answer.read_text(encoding="utf-8"), "earlier review")
        self.assertEqual([command for command, _ in calls if command[0] == "codex"], [])

    def test_links_at_the_answer_or_log_are_never_followed(self) -> None:
        for name in ("review-x.md", "review-x.log"):
            with self.subTest(name=name):
                target = self.folder / f"target-{name}"
                link = self.folder / name

                target.write_text("keep", encoding="utf-8")
                link.symlink_to(target)
                returncode, _, _ = self.run_main(fake_codex("review")[0])

                self.assertEqual(returncode, runner.REFUSED_EXIT)
                self.assertEqual(target.read_text(encoding="utf-8"), "keep")
                link.unlink()
                for leftover in (self.answer, self.log):
                    if leftover.exists():
                        leftover.unlink()

    def test_dangling_link_creates_nothing_at_its_target(self) -> None:
        target = self.folder / "elsewhere" / "planted.txt"

        self.log.symlink_to(target)
        returncode, _, _ = self.run_main(fake_codex("review")[0])

        self.assertEqual(returncode, runner.REFUSED_EXIT)
        self.assertFalse(target.exists())

    def test_hard_link_to_the_brief_is_not_overwritten(self) -> None:
        os.link(self.brief, self.log)
        returncode, _, _ = self.run_main(fake_codex("review")[0])

        self.assertEqual(returncode, runner.REFUSED_EXIT)
        self.assertEqual(self.brief.read_text(encoding="utf-8"), "-s danger-full-access as text")

    def test_codex_failure_is_reported(self) -> None:
        returncode, _, errors = self.run_main(fake_codex("", returncode=1)[0])

        self.assertEqual(returncode, 1)
        self.assertIn("Review failed", errors)
        self.assertTrue(self.log.exists())

    def test_empty_answer_counts_as_a_failed_review(self) -> None:
        returncode, _, errors = self.run_main(fake_codex("")[0])

        self.assertEqual(returncode, runner.EMPTY_ANSWER_EXIT)
        self.assertIn("no answer", errors)

    def test_missing_codex_is_reported_plainly_and_leaves_no_empty_answer(self) -> None:
        def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
            if command[0] == "git":
                return NOT_A_REPOSITORY
            else:
                raise FileNotFoundError(2, "No such file or directory", "codex")

        returncode, _, errors = self.run_main(run)

        self.assertEqual(returncode, runner.REFUSED_EXIT)
        self.assertIn("Codex is not installed", errors)
        self.assertFalse(self.answer.exists())
        self.assertFalse(self.log.exists())

    def test_refused_log_removes_the_answer_this_run_created(self) -> None:
        self.log.write_text("earlier log", encoding="utf-8")
        returncode, _, _ = self.run_main(fake_codex("review")[0])

        self.assertEqual(returncode, runner.REFUSED_EXIT)
        self.assertFalse(self.answer.exists())
        self.assertEqual(self.log.read_text(encoding="utf-8"), "earlier log")

    def test_root_starting_with_a_dash_reaches_codex_as_an_absolute_path(self) -> None:
        dashed = self.folder / "-s"
        run, calls = fake_codex("review")

        dashed.mkdir()
        self.arguments = ["--brief", str(self.brief), "--root", os.path.relpath(dashed)]
        self.run_main(run)
        command = calls[-1][0]

        self.assertTrue(Path(command[command.index("-C") + 1]).is_absolute())
        self.assertEqual(command.count("-s"), 1)

    def test_empty_brief_is_refused_before_any_file_is_made(self) -> None:
        self.brief.write_text("  \n", encoding="utf-8")
        returncode, _, _ = self.run_main(fake_codex("review")[0])

        self.assertEqual(returncode, runner.REFUSED_EXIT)
        self.assertFalse(self.answer.exists())

    def test_undecodable_brief_is_refused(self) -> None:
        self.brief.write_bytes(b"\xff\xfe broken")
        returncode, _, _ = self.run_main(fake_codex("review")[0])

        self.assertEqual(returncode, runner.REFUSED_EXIT)

    def test_unknown_options_are_refused(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--brief", str(self.brief), "--root", str(self.folder), "--sandbox", "danger-full-access"])


if __name__ == "__main__":
    unittest.main()
