"""Tests for the report shape checks and the hook that runs them after a write to the notes folder."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
HOOK_SCRIPT = SCRIPTS_DIRECTORY / "report-check-hook.py"
CHECK_SCRIPT = SCRIPTS_DIRECTORY / "report_check.py"
HOME = "/Users/someone"
NOTES = f"{HOME}/Library/Application Support/Claude/side-session-notes"
PROJECT_NOTES = f"{HOME}/Documents/session-notes"


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)

    if specification and specification.loader:
        module = importlib.util.module_from_spec(specification)

        sys.path.insert(0, str(SCRIPTS_DIRECTORY))
        sys.modules[name] = module
        specification.loader.exec_module(module)
        return module
    else:
        raise ImportError(f"Cannot load {path}")


report_check = load_module("report_check", CHECK_SCRIPT)
report_check_hook = load_module("report_check_hook", HOOK_SCRIPT)
GOOD_REPORT = """---
상태: 결정 대기
---

# 보고서는 짧은 첫 화면으로 씁니다

> [!abstract] 한눈에
> **결론.** 첫 화면에 결론과 할 일을 둡니다.
>
> | 안 | 정확성 |
> | --- | --- |
> | 가 | 높음 |

## 무엇이 문제인가

[[#부록]]에 자세한 것이 있습니다. 파일은 [보고서](file:///Users/someone/a%20b/r.md) line 3에 있습니다.

```
## 요약 (코드 안의 제목은 세지 않음)
7절
```

## 부록

> [!note]- 바뀐 과정
> 1판 요약은 12장에 있었습니다.
"""
LONG_CELL = "가" * 120
BAD_REPORT = f"""# 옛 보고서

작성: {"세션 기록 " * 300}

## 2판 요약

| 하나 | 둘 | 셋 | 넷 | 다섯 | 여섯 | 일곱 |
| --- | --- | --- | --- | --- | --- | --- |
| {LONG_CELL} | b | c | d | e | f | g |

## 1판 요약

7절과 10.1장을 보세요. 경로는 /Users/someone/x.md 입니다.
"""


def list_checks(text: str) -> list[str]:
    return [finding.check for finding in report_check.check_report(text)]


class ReportCheckTest(unittest.TestCase):
    def test_report_in_the_new_shape_passes(self) -> None:
        self.assertEqual(report_check.check_report(GOOD_REPORT), [])

    def test_old_shape_problems_are_found(self) -> None:
        self.assertEqual(
            list_checks(BAD_REPORT),
            ["preamble", "opening", "table-cell", "table-columns", "history", "section-number", "bare-path"],
        )

    def test_long_cells_after_the_opening_use_the_body_limit(self) -> None:
        body_row = "| " + "나" * 80 + " | b |"
        text = f"# 제목\n\n> [!abstract] 요약\n> 짧음\n\n## 본문\n\n| 가 | 나 |\n| --- | --- |\n{body_row}\n"

        self.assertEqual(list_checks(text), [])
        self.assertEqual(list_checks(text.replace("나" * 80, "나" * 101)), ["table-cell"])

    def test_link_targets_do_not_count_toward_cell_length(self) -> None:
        link = "[원본](file:///" + "a" * 200 + ".md)"
        text = f"# 제목\n\n> [!abstract] 요약\n>\n> | 원본 |\n> | --- |\n> | {link} |\n"

        self.assertEqual(list_checks(text), [])

    def test_opening_with_many_code_spans_is_found(self) -> None:
        text = "# 제목\n\n> [!abstract] 요약\n> `a` `b` `c` `d` 를 씁니다.\n"

        self.assertEqual(list_checks(text), ["opening-code"])

    def test_title_does_not_count_toward_the_preamble(self) -> None:
        title = "# " + "제" * 60
        preamble = "작" * 395
        text = f"---\n상태: 대기\n---\n\n{title}\n\n{preamble}\n\n## 요약\n\n짧음\n"

        self.assertEqual(list_checks(text), [])
        self.assertEqual(list_checks(text.replace(preamble, preamble + "가" * 10)), ["preamble"])

    def test_escaped_pipes_stay_inside_their_cell(self) -> None:
        text = "# 제목\n\n> [!abstract] 요약\n>\n> | 링크 | 뜻 |\n> | --- | --- |\n> | [[#가\\|나]] | 다 |\n"

        self.assertEqual(report_check._list_table_rows(text), [["링크", "뜻"], ["[[#가\\|나]]", "다"]])

    def test_only_second_level_summary_headings_count_as_history(self) -> None:
        text = "# 요약 보고: 결론\n\n> [!abstract] 요약\n> 짧음\n\n## 결론\n\n### 비교 요약\n\n본문\n"

        self.assertEqual(list_checks(text), [])

    def test_plain_paths_in_markdown_links_are_found(self) -> None:
        text = "# 제목\n\n> [!abstract] 요약\n> 짧음\n\n## 본문\n\n[보고서](/Users/someone/r.md)\n"

        self.assertEqual(list_checks(text), ["bare-path"])

    def test_windows_line_ends_read_like_unix_ones(self) -> None:
        self.assertEqual(report_check.check_report(GOOD_REPORT.replace("\n", "\r\n")), [])

    def test_fenced_code_inside_a_callout_is_left_out(self) -> None:
        text = "# 제목\n\n> [!abstract] 요약\n> 짧음\n\n## 부록\n\n> ```\n> ## 요약\n> ## 결론\n> ```\n"

        self.assertEqual(list_checks(text), [])

    def test_notes_reports_are_files_inside_a_session_folder(self) -> None:
        self.assertTrue(report_check.is_notes_report(f"{NOTES}/local_1/report.md"))
        self.assertTrue(report_check.is_notes_report(f"{NOTES}/local_1/sub/report.MD"))
        self.assertFalse(report_check.is_notes_report(f"{NOTES}/bulletin.md"))
        self.assertFalse(report_check.is_notes_report(f"{NOTES}/local_1/script.py"))
        self.assertFalse(report_check.is_notes_report("/a/README.md"))

    def test_project_notes_folders_hold_notes_reports_but_their_copies_do_not(self) -> None:
        self.assertTrue(report_check.is_notes_report(f"{PROJECT_NOTES}/ai-config/report.md"))
        self.assertTrue(report_check.is_notes_report(f"{PROJECT_NOTES}/ai-config/reviews/review.md"))
        self.assertFalse(report_check.is_notes_report(f"{PROJECT_NOTES}/top-level.md"))
        self.assertFalse(report_check.is_notes_report(f"{PROJECT_NOTES}-backup/app-notes/local_1/report.md"))
        self.assertFalse(report_check.is_notes_report(f"{PROJECT_NOTES}/ai-config/data.json"))

    def test_app_session_folder_files_are_only_inside_session_named_folders(self) -> None:
        home = Path(HOME)

        self.assertTrue(report_check.is_app_session_folder_file(f"{NOTES}/local_1/report.md", home))
        self.assertTrue(report_check.is_app_session_folder_file(f"{NOTES}/local_1/sub/data.json", home))
        self.assertTrue(report_check.is_app_session_folder_file(f"{HOME}/x/../{NOTES[len(HOME) + 1:]}/local_1/r.md", home))
        self.assertFalse(report_check.is_app_session_folder_file(f"{NOTES}/bulletin.md", home))
        self.assertFalse(report_check.is_app_session_folder_file(f"{NOTES}/.obsidian/workspace.json", home))
        self.assertFalse(report_check.is_app_session_folder_file(f"{PROJECT_NOTES}/ai-config/report.md", home))
        self.assertFalse(
            report_check.is_app_session_folder_file(
                f"{PROJECT_NOTES}-backup/2026-11-23/side-session-notes/local_1/report.md",
                home,
            ),
        )
        self.assertFalse(report_check.is_app_session_folder_file(f"/Users/other/{NOTES[len(HOME) + 1:]}/local_1/r.md", home))

    def test_command_line_reports_and_sets_its_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.md"
            bad = Path(directory) / "bad.md"

            good.write_text(GOOD_REPORT, encoding="utf-8")
            bad.write_text(BAD_REPORT, encoding="utf-8")
            passing = self._run_check(good)
            failing = self._run_check(bad)
        self.assertEqual((passing.returncode, passing.stdout.strip()), (0, "good.md: no findings"))
        self.assertEqual(failing.returncode, 1)
        self.assertIn("[history]", failing.stdout)

    def test_command_line_needs_a_file_and_reports_unreadable_ones(self) -> None:
        missing = self._run_check(Path("/nonexistent/report.md"))
        empty = subprocess.run([sys.executable, str(CHECK_SCRIPT)], check=False, capture_output=True, text=True)

        self.assertEqual((missing.returncode, missing.stdout), (1, ""))
        self.assertIn("report.md: cannot be read", missing.stderr)
        self.assertEqual(empty.returncode, 2)
        self.assertIn("usage:", empty.stderr)

    def _run_check(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), str(path)], check=False, capture_output=True, text=True
        )


class ReportCheckHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name) / "side-session-notes" / "local_1"
        self.folder.mkdir(parents=True)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_report_with_problems_gets_a_notice(self) -> None:
        report = self._write("old.md", BAD_REPORT)
        output = report_check_hook.make_output({"tool_input": {"file_path": str(report)}})
        context = output["hookSpecificOutput"]["additionalContext"] if output else ""

        self.assertTrue(context.startswith("[REPORT CHECK] old.md: "), context)
        self.assertIn("[history]", context)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"] if output else None, "PostToolUse")

    def test_good_reports_indexes_and_other_files_stay_silent(self) -> None:
        cases = (
            self._write("good.md", GOOD_REPORT),
            self._write("INDEX.md", BAD_REPORT),
            Path(self.directory.name) / "elsewhere.md",
            self.folder / "missing.md",
        )
        (Path(self.directory.name) / "elsewhere.md").write_text(BAD_REPORT, encoding="utf-8")

        for path in cases:
            self.assertIsNone(report_check_hook.make_output({"tool_input": {"file_path": str(path)}}), path)
        self.assertIsNone(report_check_hook.make_output({"tool_input": {}}))

    def test_hook_script_prints_the_notice_and_survives_bad_input(self) -> None:
        report = self._write("old.md", BAD_REPORT)
        written = self._run_hook(json.dumps({"tool_input": {"file_path": str(report)}}))
        broken = self._run_hook("not json")

        self.assertIn("[REPORT CHECK]", json.loads(written.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual((broken.returncode, broken.stdout), (0, ""))

    def _write(self, name: str, text: str) -> Path:
        path = self.folder / name

        path.write_text(text, encoding="utf-8")
        return path

    def _run_hook(self, stdin: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)], check=False, input=stdin, capture_output=True, text=True
        )


if __name__ == "__main__":
    unittest.main()
