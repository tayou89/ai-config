"""Tests for the Codex budget check that keeps reviews first and reserves the spare share."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BUDGET_SCRIPT = REPOSITORY_ROOT / "skills" / "agent-usage" / "codex-budget.py"
LIMITS_SCRIPT = REPOSITORY_ROOT / "skills" / "outside-review" / "codex-limits.py"
KST = timezone(timedelta(hours=9))
RESEARCH_FOLDER = "/repo-codex-research"
WORKTREE = "/repo-codex-topic"
REVIEW_ID = "20261116-aaaaaaaa"
IMPLEMENTATION_ID = "20261116-bbbbbbbb"
SETUP_TEXT = "# AGENTS.md instructions\n\nBudget reservation: 20261116-cccccccc"


def load_module(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)

    if specification and specification.loader:
        module = importlib.util.module_from_spec(specification)

        sys.modules[name] = module
        specification.loader.exec_module(module)
        return module
    else:
        raise ImportError(f"cannot load {path}")


budget = load_module(BUDGET_SCRIPT, "codex_budget")
limits = load_module(LIMITS_SCRIPT, "codex_limits")


def moment(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=KST)


def history(weekday: float, weekday_max: float, weekend: float = 0.0) -> object:
    return budget.ReviewHistory(weekday, weekday_max, weekend)


def make_reservation(identifier: str, kind: str, folder: str = "") -> object:
    return budget.Reservation(identifier, kind, 5.0, "topic", folder, "session", moment(16, 9), budget.STATE_RUNNING)


def make_session(thread_id: str, reservation_id: str = "", parent: str = "") -> object:
    return budget.SessionInfo(thread_id, parent, reservation_id)


def make_usage(hour: int, weight: float, cwd: str, thread_id: str) -> object:
    return budget.UsageEvent(moment(16, hour), weight, cwd, None, None, thread_id)


def user_message(text: str) -> dict[str, object]:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def write_session_log(folder: Path, brief: str, parent: str = "") -> Path:
    meta = {"id": "thread-1", "parent_thread_id": parent or None, "cwd": WORKTREE}
    usage = {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1000}}, "rate_limits": None}
    records = [
        {"type": "session_meta", "payload": meta},
        {"type": "response_item", "payload": user_message(SETUP_TEXT)},
        {"type": "response_item", "payload": user_message(brief)},
        {"timestamp": "2026-11-16T01:00:00Z", "type": "event_msg", "payload": usage},
    ]
    path = folder / "rollout.jsonl"

    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


class RemainingWorkTest(unittest.TestCase):
    def test_counts_weekday_daytime_only(self) -> None:
        expected, workdays = budget.count_remaining_work(moment(17, 14), moment(18, 9), history(11.0, 11.0))

        self.assertAlmostEqual(workdays, 6 / 11)
        self.assertAlmostEqual(expected, 6.0)

    def test_evening_start_counts_nothing_that_day(self) -> None:
        _, workdays = budget.count_remaining_work(moment(17, 21), moment(18, 9), history(11.0, 11.0))

        self.assertEqual(workdays, 0.0)

    def test_weekend_counts_only_when_reviews_happened_on_weekends(self) -> None:
        quiet = budget.count_remaining_work(moment(21, 9), moment(23, 9), history(10.0, 10.0))
        busy = budget.count_remaining_work(moment(21, 9), moment(23, 9), history(10.0, 10.0, 4.0))

        self.assertEqual(quiet, (0.0, 0.0))
        self.assertAlmostEqual(busy[0], 8.0)
        self.assertAlmostEqual(busy[1], 2.0)

    def test_blocked_claude_keeps_reserve_for_days_after_its_reset(self) -> None:
        expected, swing, workdays = budget.compute_review_reserve(
            moment(20, 19),
            moment(25, 9, 14),
            moment(23, 5),
            history(14.0, 20.0),
        )

        self.assertAlmostEqual(workdays, 2 + 14 / (11 * 60))
        self.assertAlmostEqual(expected, 14.0 * workdays)
        self.assertAlmostEqual(swing, 6.0)

    def test_swing_vanishes_when_no_work_time_is_left(self) -> None:
        expected, swing, workdays = budget.compute_review_reserve(
            moment(22, 23),
            moment(23, 9, 0),
            None,
            history(14.0, 20.0),
        )

        self.assertEqual((expected, swing, workdays), (0.0, 0.0, 0.0))


class MeasurementTest(unittest.TestCase):
    def test_event_weight_counts_growth_only(self) -> None:
        previous = {"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10}
        current = {"input_tokens": 300, "cached_input_tokens": 150, "output_tokens": 12}

        self.assertAlmostEqual(budget.event_weight(previous, current), 100 + 10 + 16)

    def test_windows_a_few_seconds_apart_are_one_window(self) -> None:
        events = [
            budget.UsageEvent(moment(17, 9), 0.0, "/a", 10.0, 1000.0),
            budget.UsageEvent(moment(17, 10), 1e6, "/a", 11.0, 1003.0),
            budget.UsageEvent(moment(17, 11), 1e6, "/a", 12.0, 999.0),
        ]

        self.assertEqual(len(budget.group_windows(events)), 1)
        self.assertAlmostEqual(budget.measure_percent_per_weight(events), 1e-6)

    def test_implementation_worktrees_are_left_out_of_review_use(self) -> None:
        now = moment(17, 12)
        events = [
            budget.UsageEvent(moment(16, 10), 1e6, "/repo", 1.0, 1.0),
            budget.UsageEvent(moment(16, 11), 3e6, "/repo-codex-topic/src", 2.0, 1.0),
            budget.UsageEvent(moment(17, 10), 5e6, "/repo", 3.0, 1.0),
        ]
        result = budget.measure_daily_reviews(events, ["/repo-codex-topic"], 1e-6, now)

        self.assertAlmostEqual(result.days[moment(16, 0).date()], 1.0)
        self.assertNotIn(now.date(), result.days)

    def test_days_without_any_codex_record_are_left_out(self) -> None:
        now = moment(19, 12)
        events = [
            budget.UsageEvent(moment(17, 10), 4e6, "/repo", 1.0, 1.0),
            budget.UsageEvent(moment(18, 10), 1e6, "/repo-codex-topic", 2.0, 1.0),
        ]
        result = budget.measure_daily_reviews(events, ["/repo-codex-topic"], 1e-6, now)

        self.assertEqual(sorted(result.days), [moment(17, 0).date(), moment(18, 0).date()])
        self.assertAlmostEqual(result.weekday_mean, 2.0)

    def test_a_window_with_one_reading_gives_no_rate(self) -> None:
        events = [budget.UsageEvent(moment(17, 9), 1e6, "/a", 10.0, 1000.0)]

        self.assertIsNone(budget.measure_percent_per_weight(events))

    def test_a_session_across_two_windows_splits_by_event(self) -> None:
        events = [
            budget.UsageEvent(moment(16, 9), 0.0, "/a", 90.0, 1000.0),
            budget.UsageEvent(moment(16, 10), 2e6, "/a", 92.0, 1000.0),
            budget.UsageEvent(moment(16, 11), 0.0, "/a", 0.0, 900000.0),
            budget.UsageEvent(moment(16, 12), 3e6, "/a", 3.0, 900000.0),
        ]

        self.assertEqual(len(budget.group_windows(events)), 2)
        self.assertAlmostEqual(budget.measure_percent_per_weight(events), 1e-6)

    def test_python_39_reads_odd_fractions_and_zulu_time(self) -> None:
        parsed = budget.read_claude_limits(1.0, "2026-11-22T20:00:00.5Z", moment(17, 9)).resets

        self.assertEqual(parsed, datetime(2026, 11, 22, 20, 0, 0, 500000, tzinfo=timezone.utc))

    def test_running_research_share_subtracts_folder_use_without_going_below_zero(self) -> None:
        now = moment(17, 12)
        reservation = budget.Reservation(
            "x",
            budget.KIND_RESEARCH,
            5.0,
            "topic",
            RESEARCH_FOLDER,
            "session",
            moment(17, 9),
            budget.STATE_RUNNING,
        )

        for weight, expected in ((2e6, 2.0), (6e6, 0.0)):
            with self.subTest(weight=weight):
                events = [
                    budget.UsageEvent(moment(17, 8), 10e6, RESEARCH_FOLDER, None, None),
                    budget.UsageEvent(moment(17, 9), 1e6, RESEARCH_FOLDER, None, None),
                    budget.UsageEvent(moment(17, 10), weight, RESEARCH_FOLDER + "/notes", None, None),
                    budget.UsageEvent(moment(17, 11), 10e6, RESEARCH_FOLDER + "-other", None, None),
                ]
                spent = budget.attribute_spending(events, {"x": reservation}, {}).get("x", 0.0) * 1e-6

                self.assertAlmostEqual(budget.unfilled_share(reservation, spent, now), expected)


class PurposeTest(unittest.TestCase):
    def test_reservation_line_is_read_from_the_brief_first_line_only(self) -> None:
        cases = (
            (f"Budget reservation: {REVIEW_ID}\n# Review brief", REVIEW_ID),
            (f"\n  Budget reservation: {REVIEW_ID}  \n# Review brief", REVIEW_ID),
            (f"# Review brief\n\nBudget reservation: {REVIEW_ID}", ""),
            ("Budget reservation: not-an-id\n# Brief", ""),
        )

        for brief, expected in cases:
            with self.subTest(brief=brief), tempfile.TemporaryDirectory() as folder:
                info, events = budget._read_session_file(write_session_log(Path(folder), brief))

                self.assertEqual((info.thread_id, info.reservation_id), ("thread-1", expected))
                self.assertEqual([event.thread_id for event in events], ["thread-1"])

    def test_parent_thread_is_read_from_the_session_header(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            info, _ = budget._read_session_file(write_session_log(Path(folder), "Approve this command?", "thread-0"))
        self.assertEqual(info.parent_thread_id, "thread-0")

    def test_review_run_counts_as_review_even_inside_an_implementation_worktree(self) -> None:
        reservations = {
            REVIEW_ID: make_reservation(REVIEW_ID, budget.KIND_REVIEW),
            IMPLEMENTATION_ID: make_reservation(IMPLEMENTATION_ID, budget.KIND_IMPLEMENTATION, WORKTREE),
        }
        purposes = budget.resolve_purposes({"check": make_session("check", REVIEW_ID)}, reservations, {})
        events = [make_usage(10, 2e6, WORKTREE + "/src", "check")]
        daily = budget.measure_daily_reviews(events, [WORKTREE], 1e-6, moment(17, 12), purposes)

        self.assertEqual(purposes["check"], budget.Purpose(budget.KIND_REVIEW, REVIEW_ID))
        self.assertAlmostEqual(daily.days[moment(16, 0).date()], 2.0)
        self.assertEqual(budget.attribute_spending(events, reservations, purposes), {REVIEW_ID: 2e6})

    def test_read_only_run_under_an_implementation_reservation_is_not_review(self) -> None:
        reservations = {IMPLEMENTATION_ID: make_reservation(IMPLEMENTATION_ID, budget.KIND_IMPLEMENTATION, WORKTREE)}
        purposes = budget.resolve_purposes({"build": make_session("build", IMPLEMENTATION_ID)}, reservations, {})
        events = [make_usage(10, 3e6, "/repo", "build")]
        daily = budget.measure_daily_reviews(events, [WORKTREE], 1e-6, moment(17, 12), purposes)

        self.assertAlmostEqual(daily.days[moment(16, 0).date()], 0.0)
        self.assertEqual(budget.attribute_spending(events, reservations, purposes), {IMPLEMENTATION_ID: 3e6})

    def test_helper_run_follows_its_parent_and_an_orphan_stays_unknown(self) -> None:
        reservations = {IMPLEMENTATION_ID: make_reservation(IMPLEMENTATION_ID, budget.KIND_IMPLEMENTATION, WORKTREE)}
        sessions = {
            "build": make_session("build", IMPLEMENTATION_ID),
            "helper": make_session("helper", parent="build"),
            "nested": make_session("nested", parent="helper"),
            "orphan": make_session("orphan", parent="gone"),
            "loop-a": make_session("loop-a", parent="loop-b"),
            "loop-b": make_session("loop-b", parent="loop-a"),
        }
        purposes = budget.resolve_purposes(sessions, reservations, {})
        expected = budget.Purpose(budget.KIND_IMPLEMENTATION, IMPLEMENTATION_ID)

        self.assertEqual((purposes["helper"], purposes["nested"]), (expected, expected))
        self.assertFalse({"orphan", "loop-a", "loop-b"} & set(purposes))

    def test_a_second_log_of_the_same_run_keeps_the_first_log_brief(self) -> None:
        line = f"Budget reservation: {REVIEW_ID}\n# Review brief"

        for earlier, later, expected in ((line, "Continue the review.", REVIEW_ID), ("Continue.", line, "")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                first = Path(folder) / "a"
                second = Path(folder) / "b"

                first.mkdir()
                second.mkdir()
                write_session_log(first, earlier)
                write_session_log(second, later)
                with mock.patch.object(budget, "CODEX_SESSIONS", Path(folder)):
                    codex_history = budget._load_codex_history(moment(1, 0))
                self.assertEqual(codex_history.sessions["thread-1"].reservation_id, expected)
                self.assertEqual(len(codex_history.events), 2)

    def test_a_closed_reservation_takes_no_use_after_it_closed(self) -> None:
        inner = budget.Reservation(
            "20261116-00000002",
            budget.KIND_RESEARCH,
            5.0,
            "topic",
            WORKTREE + "/notes",
            "session",
            moment(16, 9),
            "done",
            moment(16, 10),
        )
        outer = budget.Reservation(
            "20261116-00000001",
            budget.KIND_IMPLEMENTATION,
            5.0,
            "topic",
            WORKTREE,
            "session",
            moment(16, 11),
        )
        reservations = {item.identifier: item for item in (inner, outer)}
        events = [make_usage(9, 1e6, WORKTREE + "/notes", "a"), make_usage(12, 2e6, WORKTREE + "/notes/x", "b")]
        expected = {inner.identifier: 1e6, outer.identifier: 2e6}

        self.assertEqual(budget.attribute_spending(events, reservations, {}), expected)

    def test_open_review_holds_only_what_its_linked_run_has_not_spent(self) -> None:
        review = budget.Reservation(REVIEW_ID, budget.KIND_REVIEW, 5.0, "topic", "", "session", moment(16, 9))
        purposes = {"check": budget.Purpose(budget.KIND_REVIEW, REVIEW_ID)}
        events = [make_usage(10, 2e6, "/repo", "check")]
        claude = budget.ClaudeLimits(None, None)
        status = budget._compute_status(
            50.0,
            moment(18, 9),
            claude,
            events,
            {REVIEW_ID: review},
            moment(16, 10),
            purposes,
        )

        self.assertAlmostEqual(status.open_reserved, 5.0 - 2e6 * budget._DEFAULT_PERCENT_PER_WEIGHT)
        self.assertEqual(status.report["purpose_unknown_percent"], 0.0)

    def test_reservation_missing_from_the_ledger_leaves_the_purpose_unknown(self) -> None:
        self.assertEqual(budget.resolve_purposes({"run": make_session("run", REVIEW_ID)}, {}, {}), {})

    def test_unlinked_use_goes_to_one_old_reservation_by_its_folder(self) -> None:
        outer = make_reservation("20261116-00000001", budget.KIND_IMPLEMENTATION, WORKTREE)
        inner = make_reservation("20261116-00000002", budget.KIND_RESEARCH, WORKTREE + "/notes")
        linked = make_reservation(IMPLEMENTATION_ID, budget.KIND_IMPLEMENTATION, "/linked")
        reservations = {item.identifier: item for item in (outer, inner, linked)}
        purposes = budget.resolve_purposes({"build": make_session("build", IMPLEMENTATION_ID)}, reservations, {})
        events = [
            make_usage(8, 9e6, WORKTREE, "early"),
            make_usage(10, 1e6, WORKTREE + "/src", "a"),
            make_usage(11, 2e6, WORKTREE + "/notes/x", "b"),
            make_usage(12, 4e6, "/linked", "stray"),
            make_usage(13, 8e6, "/linked", "build"),
        ]
        spending = budget.attribute_spending(events, reservations, purposes)

        self.assertEqual(spending, {outer.identifier: 1e6, inner.identifier: 2e6, IMPLEMENTATION_ID: 8e6})

    def test_labelled_review_in_an_old_worktree_is_review_use_only(self) -> None:
        old = make_reservation("20261116-00000001", budget.KIND_IMPLEMENTATION, WORKTREE)
        reservations = {old.identifier: old}
        labels = {"check": budget.KIND_REVIEW}
        purposes = budget.resolve_purposes({"check": make_session("check")}, reservations, labels)
        events = [make_usage(10, 2e6, WORKTREE, "check")]
        daily = budget.measure_daily_reviews(events, [WORKTREE], 1e-6, moment(17, 12), purposes)

        self.assertAlmostEqual(daily.days[moment(16, 0).date()], 2.0)
        self.assertEqual(budget.attribute_spending(events, reservations, purposes), {})

    def test_labelled_builder_run_is_not_review_and_bad_label_lines_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            labels_file = Path(folder) / "labels.jsonl"
            lines = [
                json.dumps({"thread_id": "draft", "kind": budget.KIND_BUILDER, "basis": "# Brief"}),
                "not json",
                json.dumps({"thread_id": "odd", "kind": "guess"}),
                json.dumps({"thread_id": "maybe", "kind": "unknown"}),
            ]

            labels_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with mock.patch.object(budget, "LABELS_FILE", labels_file):
                labels = budget._read_labels()
            with mock.patch.object(budget, "LABELS_FILE", Path(folder) / "missing.jsonl"):
                missing = budget._read_labels()
        sessions = {"draft": make_session("draft"), "helper": make_session("helper", parent="draft")}
        purposes = budget.resolve_purposes(sessions, {}, labels)
        events = [make_usage(10, 5e6, "/repo", "draft"), make_usage(11, 1e6, "/repo", "helper")]
        daily = budget.measure_daily_reviews(events, [], 1e-6, moment(17, 12), purposes)

        self.assertEqual(labels, {"draft": budget.KIND_BUILDER})
        self.assertEqual(missing, {})
        self.assertEqual(purposes["helper"], budget.Purpose(budget.KIND_BUILDER))
        self.assertAlmostEqual(daily.days[moment(16, 0).date()], 0.0)


class VerdictTest(unittest.TestCase):
    def test_spare_share_is_used_whatever_claude_expects(self) -> None:
        self.assertEqual(budget.decide_verdict(10.0, False, 95.0), ("delegate", "extra-work"))
        self.assertEqual(budget.decide_verdict(10.0, False, 120.0), ("delegate", "offload-claude"))
        self.assertEqual(budget.decide_verdict(10.0, True, None), ("delegate", "offload-claude"))
        self.assertEqual(budget.decide_verdict(10.0, False, None), ("delegate", "unknown"))
        self.assertEqual(budget.decide_verdict(0.0, False, 80.0), ("hold", "extra-work"))

    def test_codex_errors_are_reported(self) -> None:
        self.assertTrue(budget.parse_codex_limits({"error": "down"}).error)
        self.assertTrue(budget.parse_codex_limits({"plan": "prolite"}).error)
        self.assertTrue(budget.parse_codex_limits([]).error)
        good = budget.parse_codex_limits({"seven_day": 18, "seven_day_resets": "2026-11-23T09:14+09:00"})

        self.assertEqual((good.percent, good.error), (18.0, ""))

    def test_stale_claude_cache_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "claude-limits.json"
            measured = moment(17, 9)
            entry = {"kind": "seven_day", "percentUsed": 30, "resetsAt": "2026-11-22T20:00:00Z"}

            cache.write_text(json.dumps({"at": measured.timestamp() * 1000, "limits": [entry]}), encoding="utf-8")
            with mock.patch.object(budget, "CLAUDE_LIMITS", cache):
                fresh = budget.read_claude_limits(None, None, measured + timedelta(minutes=5))
                stale = budget.read_claude_limits(None, None, measured + timedelta(hours=2))
        self.assertEqual(fresh.percent, 30.0)
        self.assertIsNone(stale.percent)


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        root = Path(folder.name)
        resets = datetime.now(timezone.utc) + timedelta(hours=1)
        replacements = {
            "LEDGER_DIRECTORY": root,
            "LEDGER_FILE": root / "ledger.jsonl",
            "LOCK_FILE": root / "ledger.lock",
            "LABELS_FILE": root / "labels.jsonl",
            "_read_codex_limits": lambda: budget.CodexLimits(75.0, resets, ""),
            "_load_codex_history": lambda since: budget.CodexHistory([], {}),
        }

        self.addCleanup(folder.cleanup)
        for name, value in replacements.items():
            patcher = mock.patch.object(budget, name, value)

            patcher.start()
            self.addCleanup(patcher.stop)

    def reserve_arguments(self, kind: str, percent: float) -> argparse.Namespace:
        return argparse.Namespace(
            kind=kind,
            percent=percent,
            topic="topic",
            worktree="/nowhere-codex-topic" if kind != budget.KIND_REVIEW else "",
            session="session",
            claude_percent=40.0,
            claude_resets="2026-11-22T20:00:00Z",
        )

    def reserve(self, kind: str, percent: float) -> tuple[int, dict[str, object]]:
        output = io.StringIO()

        with redirect_stdout(output):
            code = budget.run_reserve(self.reserve_arguments(kind, percent))
        answer: dict[str, object] = json.loads(output.getvalue())

        return code, answer

    def update(self, identifier: str, state: str) -> dict[str, object]:
        output = io.StringIO()

        with redirect_stdout(output):
            budget.run_update(argparse.Namespace(identifier=identifier, state=state))
        answer: dict[str, object] = json.loads(output.getvalue())

        return answer

    def test_two_sessions_cannot_spend_the_same_share(self) -> None:
        results: list[int] = []
        arguments = self.reserve_arguments("implementation", 20.0)
        threads = [threading.Thread(target=lambda: results.append(budget.run_reserve(arguments))) for _ in range(2)]

        with redirect_stdout(io.StringIO()):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(results), [0, budget.EXIT_REFUSED])

    def test_research_reservation_without_a_folder_is_refused(self) -> None:
        arguments = [str(BUDGET_SCRIPT), "reserve", "--kind", budget.KIND_RESEARCH, "--percent", "2", "--topic", "topic"]
        errors = io.StringIO()

        with mock.patch.object(sys, "argv", arguments), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                budget.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("a research reservation needs --worktree or --folder", errors.getvalue())
        self.assertFalse(budget.LEDGER_FILE.exists())

    def test_research_folder_alias_leaves_its_use_out_of_daily_reviews(self) -> None:
        now = moment(17, 12)
        parser = budget._build_parser()
        options = [
            "reserve",
            "--kind",
            budget.KIND_RESEARCH,
            "--percent",
            "2",
            "--topic",
            "topic",
            "--folder",
            RESEARCH_FOLDER,
            "--claude-percent",
            "40",
            "--claude-resets",
            "2026-11-22T20:00:00Z",
        ]
        arguments = parser.parse_args(options)
        events = [
            budget.UsageEvent(moment(16, 10), 1e6, "/repo", 1.0, 1.0),
            budget.UsageEvent(moment(16, 11), 3e6, RESEARCH_FOLDER + "/notes", 4.0, 1.0),
            budget.UsageEvent(moment(17, 10), 5e6, "/repo", 9.0, 1.0),
        ]

        budget._check_reserve_arguments(parser, arguments)
        with mock.patch.object(budget, "_now_local", return_value=moment(16, 9)), redirect_stdout(io.StringIO()):
            self.assertEqual(budget.run_reserve(arguments), 0)
        reservations = budget._read_reservations(now)
        reservation = next(iter(reservations.values()))
        status = budget._compute_status(
            75.0,
            moment(18, 9),
            budget.ClaudeLimits(None, None),
            events,
            reservations,
            now,
            {},
        )

        self.assertEqual(reservation.kind, budget.KIND_RESEARCH)
        self.assertEqual(reservation.worktree, RESEARCH_FOLDER)
        self.assertAlmostEqual(status.report["review_per_weekday"], 1.0)
        self.assertAlmostEqual(status.report["purpose_unknown_percent"], 9.0)

    def test_research_review_waiting_and_done_hold_no_share(self) -> None:
        code, answer = self.reserve(budget.KIND_RESEARCH, 20.0)

        self.assertEqual(code, 0)
        for state in (budget.STATE_REVIEW_WAITING, "done"):
            with self.subTest(state=state):
                self.assertTrue(self.update(str(answer["id"]), state)["updated"])
                now = datetime.now(timezone.utc)
                arguments = self.reserve_arguments(budget.KIND_RESEARCH, 20.0)
                status = budget._load_status(arguments, now, budget._read_codex_limits())

                self.assertEqual(status.open_reserved, 0.0)
                self.assertEqual(status.available, 25.0)

    def test_closing_returns_the_share_once(self) -> None:
        _, first = self.reserve("implementation", 20.0)

        self.assertEqual(self.reserve("implementation", 20.0)[0], budget.EXIT_REFUSED)
        self.assertTrue(self.update(str(first["id"]), "failed")["updated"])
        self.assertFalse(self.update(str(first["id"]), "done")["updated"])
        self.assertEqual(self.reserve("implementation", 20.0)[0], 0)

    def test_review_may_use_the_review_reserve(self) -> None:
        self.reserve("implementation", 20.0)

        self.assertEqual(self.reserve("review", 3.0)[0], 0)
        self.assertEqual(self.reserve("review", 3.0)[0], budget.EXIT_REFUSED)

    def test_unstarted_reservations_expire(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=budget.RESERVED_EXPIRY_HOURS + 1)
        reservation = budget.Reservation("x", "implementation", 20.0, "t", "/w", "s", old)

        self.assertTrue(budget.is_expired(reservation, datetime.now(timezone.utc)))
        self.assertEqual(budget.unfilled_share(reservation, 0.0, datetime.now(timezone.utc)), 0.0)

    def test_running_entries_left_open_expire(self) -> None:
        now = datetime.now(timezone.utc)
        started = now - timedelta(hours=budget.RUNNING_EXPIRY_HOURS + 1)
        reservation = budget.Reservation("x", "implementation", 20.0, "t", "/w", "s", started, "running", started)

        self.assertTrue(budget.is_expired(reservation, now))

    def test_broken_ledger_lines_are_skipped(self) -> None:
        _, first = self.reserve("implementation", 10.0)

        with budget.LEDGER_FILE.open("a", encoding="utf-8") as handle:
            handle.write('not json\n[1, 2]\n{"event": "reserve", "identifier": "y"}\n')
        self.assertTrue(self.update(str(first["id"]), "done")["updated"])

    def test_closing_a_review_records_the_use_of_its_linked_run(self) -> None:
        code, answer = self.reserve(budget.KIND_REVIEW, 3.0)
        identifier = str(answer["id"])
        events = [budget.UsageEvent(datetime.now(timezone.utc), 2e6, "/repo", None, None, "run")]
        codex_history = budget.CodexHistory(events, {"run": budget.SessionInfo("run", "", identifier)})

        with mock.patch.object(budget, "_load_codex_history", lambda since: codex_history):
            closed = self.update(identifier, "done")
        self.assertEqual(code, 0)
        self.assertAlmostEqual(float(str(closed["actual_percent"])), 2.74)

    def test_a_new_reservation_id_matches_both_line_readers(self) -> None:
        _, answer = self.reserve(budget.KIND_REVIEW, 3.0)
        identifier = str(answer["id"])
        found = limits.RESERVATION_LINE.search(f"codex exec -- Budget reservation: {identifier}\\012# Brief")

        self.assertEqual(budget._read_reservation_line(f"Budget reservation: {identifier}\n# Brief"), identifier)
        self.assertEqual(found.group(1) if found else "", identifier)

    def test_a_run_of_a_reservation_past_the_history_cutoff_keeps_its_purpose(self) -> None:
        now = datetime.now(timezone.utc).astimezone()
        created = now - timedelta(days=budget._HISTORY_DAYS, hours=2)
        record = {
            "event": "reserve",
            "identifier": IMPLEMENTATION_ID,
            "kind": budget.KIND_IMPLEMENTATION,
            "percent": 5.0,
            "worktree": WORKTREE,
            "created": created.isoformat(),
            "state": "done",
            "changed": (created + timedelta(minutes=30)).isoformat(),
        }
        events = [budget.UsageEvent(now - timedelta(days=budget._HISTORY_DAYS), 3e6, "/repo", None, None, "build")]
        codex_history = budget.CodexHistory(events, {"build": budget.SessionInfo("build", "", IMPLEMENTATION_ID)})
        arguments = self.reserve_arguments(budget.KIND_REVIEW, 1.0)

        budget.LEDGER_FILE.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with mock.patch.object(budget, "_load_codex_history", lambda since: codex_history):
            status = budget._load_status(arguments, now, budget._read_codex_limits())
        self.assertEqual((status.report["review_per_weekday"], status.report["review_per_weekend_day"]), (0.0, 0.0))

    def test_closing_an_unlinked_review_records_no_use(self) -> None:
        _, answer = self.reserve(budget.KIND_REVIEW, 3.0)

        self.assertIsNone(self.update(str(answer["id"]), "done")["actual_percent"])


class ReviewCountTest(unittest.TestCase):
    def test_only_read_only_runs_count_as_reviews(self) -> None:
        self.assertTrue(limits.is_review_process(["/usr/bin/codex", "exec", "-s", "read-only", "brief"]))
        self.assertTrue(limits.is_review_process(["codex", "exec", "--sandbox=read-only"]))
        self.assertFalse(limits.is_review_process(["codex", "exec", "-s", "workspace-write", "brief"]))
        self.assertFalse(limits.is_review_process(["codex", "app-server"]))

    def test_reservation_line_decides_before_the_sandbox_mode(self) -> None:
        kinds = {REVIEW_ID: budget.KIND_REVIEW, IMPLEMENTATION_ID: budget.KIND_IMPLEMENTATION}
        line = ["--", "Budget", "reservation:"]
        review = ["codex", "exec", "-s", "workspace-write", *line, REVIEW_ID + "\\012#", "Brief"]
        build = ["codex", "exec", "-s", "read-only", *line, IMPLEMENTATION_ID]
        unknown = ["codex", "exec", "-s", "read-only", *line, "20261116-dddddddd"]
        quoted = ["codex", "exec", "-s", "workspace-write", "--", "#", "Brief", *line[1:], REVIEW_ID]
        brief_start = ["codex", "exec", "-s", "read-only", "--", "#", "Brief\\012"]
        example = [*brief_start, "codex", "exec", *line, IMPLEMENTATION_ID]
        longer = ["codex", "exec", "-s", "workspace-write", *line, REVIEW_ID + "-wrong"]

        self.assertTrue(limits.is_review_process(review, kinds))
        self.assertFalse(limits.is_review_process(build, kinds))
        self.assertTrue(limits.is_review_process(unknown, kinds))
        self.assertFalse(limits.is_review_process(quoted, kinds))
        self.assertTrue(limits.is_review_process(example, kinds))
        self.assertFalse(limits.is_review_process(longer, kinds))

    def test_ledger_kinds_come_from_reserve_records_only(self) -> None:
        records = [
            json.dumps({"event": "reserve", "identifier": REVIEW_ID, "kind": budget.KIND_REVIEW}),
            "not json",
            "[1]",
            json.dumps({"event": "state", "identifier": IMPLEMENTATION_ID, "kind": budget.KIND_IMPLEMENTATION}),
        ]

        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / "ledger.jsonl"

            ledger.write_text("\n".join(records) + "\n", encoding="utf-8")
            kinds = limits.read_reservation_kinds(str(ledger))
            missing = limits.read_reservation_kinds(str(Path(folder) / "missing.jsonl"))
        self.assertEqual(kinds, {REVIEW_ID: budget.KIND_REVIEW})
        self.assertEqual(missing, {})


if __name__ == "__main__":
    unittest.main()
