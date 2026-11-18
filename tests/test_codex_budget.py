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

                self.assertAlmostEqual(budget.unfilled_share(reservation, events, 1e-6, now), expected)


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
            "_read_codex_limits": lambda: budget.CodexLimits(75.0, resets, ""),
            "_load_usage_events": lambda since: [],
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
        )

        self.assertEqual(reservation.kind, budget.KIND_RESEARCH)
        self.assertEqual(reservation.worktree, RESEARCH_FOLDER)
        self.assertAlmostEqual(status.report["review_per_weekday"], 1.0)

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
        self.assertEqual(budget.unfilled_share(reservation, [], 1e-6, datetime.now(timezone.utc)), 0.0)

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


class ReviewCountTest(unittest.TestCase):
    def test_only_read_only_runs_count_as_reviews(self) -> None:
        self.assertTrue(limits.is_review_process(["/usr/bin/codex", "exec", "-s", "read-only", "brief"]))
        self.assertTrue(limits.is_review_process(["codex", "exec", "--sandbox=read-only"]))
        self.assertFalse(limits.is_review_process(["codex", "exec", "-s", "workspace-write", "brief"]))
        self.assertFalse(limits.is_review_process(["codex", "app-server"]))


if __name__ == "__main__":
    unittest.main()
