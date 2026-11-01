"""Preparation contracts: real budget, fake time, no live services or data."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

from app.investigation import BudgetExceeded, InvestigationLimits, _Budget
from app.investigation_state import (
    InvestigationRequest,
    InvestigationRuntime,
    initial_investigation_state,
    snapshot_budget,
)
from app.router import QueryResult, RoutePlan


class InvestigationStateTests(unittest.TestCase):
    def test_state_updates_do_not_cross_requests_or_duplicate_result_ownership(self):
        first = initial_investigation_state(InvestigationRequest("预测峰值是多少？"))
        second = initial_investigation_state(InvestigationRequest("MAE 是多少？"))
        original_attempts = first["attempted"]
        result = QueryResult(
            request_id="existing-query-id", question=first["request"].question,
            as_of_utc=None, plan=RoutePlan((), (), None),
        )
        first["attempted"] = first["attempted"] | {"archived_forecast"}
        first["result"] = result
        first["before_fingerprint"] = (("q1", "not_supported", (), "[]"),)
        result.investigation_rounds = 1

        self.assertEqual(original_attempts, frozenset())
        self.assertEqual(second["attempted"], frozenset())
        self.assertIsNone(second["result"])
        self.assertIsNone(second["before_fingerprint"])
        self.assertEqual(first["result"].request_id, "existing-query-id")
        self.assertEqual(first["result"].investigation_rounds, 1)
        self.assertNotIn("request_id", first)
        self.assertNotIn("investigation_rounds", first)

    def test_original_input_and_two_time_scopes_survive_without_normalization(self):
        question = "  你好，预测峰值？不是现在。\n"
        for thinking in (None, False, True):
            with self.subTest(thinking=thinking):
                request = InvestigationRequest(
                    question, as_of_utc="2026-08-03T09:48:00+08:00",
                    knowledge_checked_on_or_before="2026-09-28",
                    use_llm=True, thinking_enabled=thinking,
                )
                state = initial_investigation_state(request)
                self.assertIs(state["request"], request)
                self.assertEqual(request.question, question)
                self.assertEqual(request.as_of_utc, "2026-08-03T09:48:00+08:00")
                self.assertEqual(request.knowledge_checked_on_or_before, "2026-09-28")
                self.assertIs(request.thinking_enabled, thinking)
                self.assertTrue(state["llm_available"])
                with self.assertRaises(FrozenInstanceError):
                    request.as_of_utc = "2030-01-01T00:00:00Z"

    def test_request_container_defers_validation_to_existing_entrypoint(self):
        request = InvestigationRequest("", as_of_utc="not-a-timestamp")
        state = initial_investigation_state(request)
        self.assertEqual(state["request"].as_of_utc, "not-a-timestamp")
        self.assertFalse(state["llm_available"])
        self.assertIsNone(state["budget_snapshot"])

    def test_snapshot_reads_do_not_reserve_and_follow_the_live_budget(self):
        budget = _Budget(InvestigationLimits(), lambda: 10.0)
        before = snapshot_budget(budget)
        self.assertEqual((budget.tool_calls, budget.llm_calls), (0, 0))
        self.assertIsNone(budget.exceeded)

        budget.reserve_tool("archived_forecast")
        budget.reserve_llm(object())
        after = snapshot_budget(budget)
        self.assertEqual((after.tool_calls, after.llm_calls), (1, 1))
        self.assertEqual((before.tool_calls, before.llm_calls), (0, 0))
        self.assertEqual(snapshot_budget(budget), after)
        self.assertEqual((budget.tool_calls, budget.llm_calls), (1, 1))
        with self.assertRaises(FrozenInstanceError):
            after.tool_calls = 0

    def test_snapshot_does_not_reset_clock_or_mark_unchecked_expiry(self):
        elapsed = [100.0]
        budget = _Budget(InvestigationLimits(max_seconds=60), lambda: elapsed[0])
        elapsed[0] = 112.5
        self.assertEqual(snapshot_budget(budget).remaining_seconds, 47.5)
        elapsed[0] = 161.0
        expired = snapshot_budget(budget)
        self.assertEqual(expired.remaining_seconds, -1.0)
        self.assertIsNone(expired.exceeded)
        self.assertIsNone(budget.exceeded)
        self.assertEqual(budget.started, 100.0)
        with self.assertRaises(BudgetExceeded):
            budget.check_time()
        self.assertEqual(snapshot_budget(budget).exceeded, "time_limit")
        self.assertIsNone(expired.exceeded)

    def test_snapshot_preserves_tool_and_model_exhaustion_without_refilling(self):
        for kind, cap in (("tool", 1), ("llm", 2)):
            with self.subTest(kind=kind):
                limits = InvestigationLimits(max_tool_calls=1, max_llm_calls=2)
                budget = _Budget(limits, lambda: 0.0)
                reserve = (lambda: budget.reserve_tool("knowledge")) if kind == "tool" else (lambda: budget.reserve_llm(object()))
                for _ in range(cap):
                    reserve()
                before = snapshot_budget(budget)
                with self.assertRaises(BudgetExceeded):
                    reserve()
                after = snapshot_budget(budget)
                self.assertEqual(after.exceeded, f"{kind}_limit")
                self.assertEqual((after.tool_calls, after.llm_calls),
                                 (before.tool_calls, before.llm_calls))
                self.assertIsNone(before.exceeded)
                with self.assertRaises(BudgetExceeded):
                    reserve()

    def test_runtime_keeps_live_dependencies_without_opening_paths(self):
        budget = _Budget(InvestigationLimits(), lambda: 0.0)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "not-created"
            runtime = InvestigationRuntime(
                provider=None, budget=budget,
                forecast_db=root / "forecast.sqlite3",
                sonar_db=root / "sonar.sqlite3", catalog=root / "catalog.json",
                root=root, audit_log=root / "logs" / "requests.jsonl",
            )
            self.assertFalse(root.exists())
            self.assertIs(runtime.budget, budget)
            runtime.budget.reserve_tool("historical_water")
            self.assertEqual(budget.tool_calls, 1)
            self.assertEqual(snapshot_budget(runtime.budget).tool_calls, 1)
            self.assertFalse(root.exists())
            with self.assertRaises(FrozenInstanceError):
                runtime.budget = _Budget(InvestigationLimits(), lambda: 0.0)

    def test_isolated_import_and_initialization_do_not_access_services_or_files(self):
        # A clean interpreter catches runtime app imports even if the ordinary
        # test runner has already loaded those modules. -B prevents pyc writes.
        project_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent("""
            import importlib
            import io
            from pathlib import Path
            import socket
            import sqlite3
            import sys
            import urllib.request
            from unittest.mock import patch

            sys.path.insert(0, sys.argv[1])
            def forbidden(*args, **kwargs):
                raise AssertionError('Unexpected IO during import/initialization')

            with patch('builtins.open', side_effect=forbidden), \
                 patch.object(io, 'open', side_effect=forbidden), \
                 patch.object(Path, 'mkdir', side_effect=forbidden), \
                 patch.object(sqlite3, 'connect', side_effect=forbidden), \
                 patch.object(socket.socket, 'connect', side_effect=forbidden), \
                 patch.object(urllib.request, 'urlopen', side_effect=forbidden):
                module = importlib.import_module('app.investigation_state')
                state = module.initial_investigation_state(
                    module.InvestigationRequest('原始问题', use_llm=True))
                assert state['result'] is None
                assert state['budget_snapshot'] is None
                assert state['llm_available'] is True

            forbidden_modules = {
                'app.investigation', 'app.router', 'app.rag', 'app.archive',
                'app.replay', 'app.read_tools', 'app.knowledge',
            }
            assert not (forbidden_modules & set(sys.modules))
            assert not any(name.split('.')[0] in {'langgraph', 'langchain', 'numpy', 'pydantic'}
                           for name in sys.modules)
        """)
        with TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-B", "-c", script, str(project_root)],
                cwd=directory, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
