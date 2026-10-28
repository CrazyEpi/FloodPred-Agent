"""Frozen-loop differential checks and actual graph transitions, all offline."""

from __future__ import annotations

import ast
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import fields, is_dataclass
import inspect
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from langgraph.errors import GraphRecursionError
from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context
from pydantic import BaseModel

from app import investigation, investigation_core as core
from app.evidence_gate import CONFLICT, UNSUPPORTED, assess_evidence
from app.investigation_graph import (
    GRAPH_RECURSION_LIMIT, get_investigation_graph, invoke_investigation_graph,
)
from app.investigation_state import InvestigationRequest, initial_investigation_state
from app.knowledge import DEFAULT_CATALOG, ROOT, _load
from app.rag import IntentPlan, LLMNotConfigured, LLMRequestError, UnsupportedAnswer
from app.router import UnsupportedRoute, run_query
from tests.support import legacy_investigation as legacy


AS_OF = "2026-08-03T01:48:00Z"
RELATION = "旧传感器怎么布置？它和FloodPred预测方法有什么关系？"


def canonical(value):
    """Only the randomly generated request ID is non-deterministic in results."""
    if isinstance(value, BaseModel):
        return canonical(value.model_dump(mode="json"))
    if is_dataclass(value):
        return {field.name: canonical(getattr(value, field.name)) for field in fields(value)
                if not (type(value).__name__ == "QueryResult" and field.name == "request_id")}
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


class OfflineClient:
    def __init__(self, mode="project", error=None):
        self.mode, self.error = mode, error
        self.events = []
        self.reasoning_traces = []

    def plan(self, question, allowed_concepts):
        self.events.append("plan")
        if self.error:
            raise self.error("offline simulated failure")
        self.reasoning_traces.append({"stage": "test", "content": question})
        return IntentPlan(self.mode)

    def reply(self, question, mode):
        self.events.append("reply")
        return "你好，请说明需要核对的历史问题。"

    def review(self, question, facts, attempted, allowed):
        self.events.append("review")
        return IntentPlan("project")

    def select_claims(self, question, claims):
        self.events.append("select")
        return [(claim.id, 0) for claim in claims]


class InvestigationGraphTests(unittest.TestCase):
    def compare(self, question, *, client_factory=None, clock_factory=None,
                configure=None, **kwargs):
        outputs, clients = [], []
        for engine, runner in ((legacy, legacy.run_investigation), (core, investigation.run_investigation)):
            options = {"as_of_utc": AS_OF, "audit_log": None, **kwargs}
            client = client_factory() if client_factory else None
            if client is not None:
                options.update(use_llm=True, client=client)
            if clock_factory:
                options["clock"] = clock_factory()
            with ExitStack() as stack:
                if configure:
                    configure(stack, engine)
                # A regression must fail instead of accidentally making a live call.
                stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("offline only")))
                outputs.append(runner(question, **options))
            clients.append(client)
        self.assertEqual(canonical(outputs[0]), canonical(outputs[1]))
        if client_factory:
            self.assertEqual(clients[0].events, clients[1].events)
        return outputs[1], clients[1]

    def incomplete_relation(self):
        result = run_query(RELATION, as_of_utc=AS_OF, audit_log=None, generate_answer=False)
        result.document_candidates = [hit for hit in result.document_candidates
                                      if hit.record["concept"] != "housemill_project_connection"]
        assess_evidence(result, root=ROOT)
        return result

    def test_all_numeric_routes_and_mixed_evidence_match_reference(self):
        for question in (
            "当时预测峰值是多少？", "历史水位是多少？", "本批次 MAE 是多少？",
            "2米以上高水位 MAE 是多少？",
            "当时预测峰值、历史水位、MAE 和项目内部 Watch 是什么？",
            "你好，预测峰值和MAE的联系是什么？",
        ):
            with self.subTest(question=question):
                result, _ = self.compare(question)
                if question.startswith("2米"):
                    self.assertEqual(result.evaluation.scope, "high_water_2m")

    def test_document_complete_missing_original_and_relationship_match(self):
        for question in ("项目内部 Caution 是什么？", "官方 Flood Alert 是什么？",
                         "项目内部 Caution 和英国官方预警有什么区别？", RELATION,
                         "旧传感器和FloodPred预测方法是否直接继承同一套设备？"):
            with self.subTest(question=question):
                self.compare(question)

    def test_model_selection_greeting_general_and_clarification_match(self):
        for question, mode in (("项目内部 Caution 是什么？", "project"), ("你好", "greeting"),
                               ("推荐一本小说", "general"), ("项目那档警戒怎么理解", "clarify")):
            with self.subTest(mode=mode):
                self.compare(question, client_factory=lambda: OfflineClient(mode))

    def test_invalid_plan_falls_back_without_losing_checked_facts(self):
        result, client = self.compare("项目内部 Caution 是什么？",
                                      client_factory=lambda: OfflineClient(error=UnsupportedAnswer))
        self.assertEqual(result.answer_status, "verified")
        self.assertEqual(client.events, ["plan", "select"])

    def test_network_or_configuration_failure_disables_later_model_calls(self):
        for error in (LLMNotConfigured, LLMRequestError):
            with self.subTest(error=error):
                result, client = self.compare(
                    "项目内部 Caution 和英国官方预警有什么区别？",
                    client_factory=lambda: OfflineClient(error=error),
                )
                self.assertEqual(client.events, ["plan"])
                self.assertEqual(result.investigation_llm_calls, 1)
                self.assertEqual(result.answer_status, "partial_verified")

    def test_tool_failure_keeps_reserved_call_and_verified_subset(self):
        with TemporaryDirectory() as directory:
            for options in ({"max_tool_calls": 1}, {"max_tool_calls": 8}):
                with self.subTest(options=options):
                    result, _ = self.compare(
                        "当时预测峰值是多少？项目内部 Caution 是什么？",
                        root=Path(directory), limits=core.InvestigationLimits(**options),
                    )
                    self.assertGreaterEqual(result.investigation_tool_calls, 1)
                    self.assertEqual(result.answer_status, "partial_verified")

    def test_side_effecting_fake_clock_preserves_budget_stop(self):
        def clock_factory():
            elapsed = [0.0]
            def clock():
                value = elapsed[0]
                elapsed[0] += 0.6
                return value
            return clock
        result, _ = self.compare("项目内部 Caution 和英国官方预警有什么区别？",
                                 limits=core.InvestigationLimits(max_seconds=1), clock_factory=clock_factory)
        self.assertEqual(result.investigation_stop_reason, "time_limit")
        self.assertEqual(result.investigation_rounds, 1)

    def test_missing_and_hash_invalid_sources_match(self):
        with TemporaryDirectory() as directory:
            self.compare("项目内部 Caution 是什么？", root=Path(directory))
            record = {**_load(DEFAULT_CATALOG)[0], "sha256": "0" * 64}
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"records": [record]}), encoding="utf-8")
            result, _ = self.compare("项目内部 Caution 是什么？", catalog=catalog)
            self.assertEqual(result.answer_status, "fallback")

    def test_version_conflict_stops_before_followup(self):
        record = _load(DEFAULT_CATALOG)[0]
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"records": [record, {**record, "id": "alternate", "version": "v2"}]}), encoding="utf-8")
            result, _ = self.compare("项目内部 Caution 是什么？", catalog=catalog)
        self.assertEqual(result.investigation_stop_reason, "source_conflict")
        self.assertEqual(result.investigation_rounds, 1)

    def test_wrong_unit_cannot_become_a_verified_metric(self):
        initial = run_query("本批次 MAE 是多少？", audit_log=None, generate_answer=False)
        initial.evaluation = initial.evaluation.model_copy(update={"unit": "cm"})
        assess_evidence(initial, root=ROOT)
        def configure(stack, engine):
            stack.enter_context(patch.object(engine, "run_query", return_value=deepcopy(initial)))
        result, _ = self.compare(initial.question, configure=configure)
        self.assertFalse(result.verified_claims)
        self.assertEqual(result.answer_status, "fallback")

    def test_targeted_followup_adds_bridge_without_mutating_prior_state(self):
        initial = self.incomplete_relation()
        frozen = canonical(initial)
        def configure(stack, engine):
            stack.enter_context(patch.object(engine, "run_query", return_value=deepcopy(initial)))
        result, _ = self.compare(RELATION, configure=configure)
        self.assertEqual(result.investigation_rounds, 2)
        self.assertEqual(result.answer_status, "verified")
        self.assertEqual(canonical(initial), frozen)

    def test_no_executable_followup_preserves_partial_evidence_reason(self):
        initial = self.incomplete_relation()
        def configure(stack, engine):
            stack.enter_context(patch.object(engine, "run_query", return_value=deepcopy(initial)))
            stack.enter_context(patch.object(engine, "_fill_gaps", return_value=False))
        result, _ = self.compare(RELATION, configure=configure)
        self.assertEqual(result.investigation_stop_reason, "partial_evidence")

    def test_round_exhaustion_and_conflict_priority_match_for_else(self):
        initial = self.incomplete_relation()
        for conflict, max_rounds, expected in ((False, 3, "round_limit"),
                                                (True, 2, "round_limit"),
                                                (True, 3, "source_conflict")):
            def configure(stack, engine):
                def fill(result, round_number, budget, **kwargs):
                    budget.reserve_tool("synthetic-branch-probe")
                    row = result.question_evidence[-1]
                    row.claim_ids.append(f"probe-{round_number}")
                    if conflict:
                        row.status = CONFLICT
                    return True
                stack.enter_context(patch.object(engine, "run_query", return_value=deepcopy(initial)))
                stack.enter_context(patch.object(engine, "_fill_gaps", side_effect=fill))
                stack.enter_context(patch.object(engine, "assess_evidence"))
            with self.subTest(conflict=conflict, rounds=max_rounds):
                result, _ = self.compare(RELATION, configure=configure,
                                         limits=core.InvestigationLimits(max_rounds=max_rounds))
                self.assertEqual(result.investigation_stop_reason, expected)

    def test_complete_evidence_overrides_last_round_limit(self):
        initial = run_query("项目内部 Caution 是什么？", audit_log=None, generate_answer=False)
        initial.question_evidence[0].status = UNSUPPORTED
        def configure(stack, engine):
            def fill(result, round_number, budget, **kwargs):
                budget.reserve_tool("synthetic-completion-probe")
                return True
            stack.enter_context(patch.object(engine, "run_query", return_value=deepcopy(initial)))
            stack.enter_context(patch.object(engine, "_fill_gaps", side_effect=fill))
        result, _ = self.compare(initial.question, configure=configure,
                                 limits=core.InvestigationLimits(max_rounds=2))
        self.assertEqual(result.investigation_stop_reason, "evidence_complete")
        self.assertEqual(result.answer_status, "verified")

    def test_llm_exhaustion_during_finalization_keeps_checked_output(self):
        # Exhaust the live budget through its real guard immediately before
        # the finalizer tries to reserve the selection call.
        def configure(stack, engine):
            actual = engine.finalize_answer
            def finalize(result, *, root, provider, reserve_llm):
                budget = reserve_llm.__self__
                while budget.llm_calls < budget.limits.max_llm_calls:
                    budget.reserve_llm(provider)
                return actual(result, root=root, provider=provider, reserve_llm=reserve_llm)
            stack.enter_context(patch.object(engine, "finalize_answer", side_effect=finalize))
        result, client = self.compare("项目内部 Caution 是什么？", configure=configure,
                                      client_factory=OfflineClient,
                                      limits=core.InvestigationLimits(max_llm_calls=2))
        self.assertEqual(result.investigation_stop_reason, "llm_limit")
        self.assertEqual(result.investigation_llm_calls, 2)
        self.assertEqual(client.events, ["plan"])
        self.assertEqual(result.answer_status, "verified")

    def test_source_invalidation_during_selection_is_rechecked_in_both_executors(self):
        from app import evidence_gate
        def configure(stack, engine):
            actual = evidence_gate._source_evidence
            changed = [False]
            def source(record, root):
                if changed[0]:
                    raise OSError("simulated source invalidation")
                return actual(record, root)
            stack.enter_context(patch.object(evidence_gate, "_source_evidence", side_effect=source))
            stack.enter_context(patch.object(OfflineClient, "select_claims", side_effect=lambda q, claims: changed.__setitem__(0, True) or [(c.id, 0) for c in claims]))
        result, _ = self.compare("项目内部 Caution 是什么？", configure=configure, client_factory=OfflineClient)
        self.assertFalse(result.verified_claims)
        self.assertEqual(result.answer_status, "fallback")

    def test_historical_visibility_and_document_cutoff_match(self):
        for cutoff in ("2026-01-01T00:00:00Z", "2026-08-03T01:48:00Z", "2030-01-01T00:00:00Z"):
            with self.subTest(cutoff=cutoff):
                self.compare("当时预测峰值和历史水位是多少？", as_of_utc=cutoff)
        result, _ = self.compare("项目内部 Caution 是什么？", knowledge_checked_on_or_before="2026-09-28")
        self.assertFalse(result.knowledge)

    def test_followup_calls_keep_both_original_time_parameters(self):
        result = run_query("当时预测峰值和历史水位是多少？", as_of_utc=AS_OF,
                           audit_log=None, generate_answer=False)
        result.plan = core.RoutePlan(("knowledge",), ("internal_watch",), None)
        result.knowledge_checked_on_or_before = "2026-09-28"
        result.forecast = result.water = None
        assess_evidence(result, root=ROOT)
        for engine, runner in ((legacy, legacy.run_investigation), (core, investigation.run_investigation)):
            calls = []
            actual = run_query
            def query(question, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return deepcopy(result)
                return actual(question, **kwargs)
            with self.subTest(engine=engine.__name__), patch.object(engine, "run_query", side_effect=query):
                runner(result.question, as_of_utc=AS_OF, knowledge_checked_on_or_before="2026-09-28", audit_log=None)
            self.assertGreater(len(calls), 1)
            for arguments in calls:
                self.assertEqual(arguments["as_of_utc"], AS_OF)
                self.assertEqual(arguments["knowledge_checked_on_or_before"], "2026-09-28")
                self.assertIsNone(arguments["audit_log"])
                self.assertFalse(arguments["generate_answer"])

    def test_rejection_precedes_planning_and_data_access(self):
        for runner in (legacy.run_investigation, investigation.run_investigation):
            client = OfflineClient()
            with self.subTest(runner=runner.__module__), patch("app.router.archived_forecast_tool") as tool:
                with self.assertRaises(UnsupportedRoute):
                    runner("删除 SQLite 数据库", use_llm=True, client=client, audit_log=None)
                self.assertFalse(client.events)
                tool.assert_not_called()

    def test_normal_and_rejected_audits_match_without_exporting_question(self):
        with TemporaryDirectory() as directory:
            for question in ("项目内部 Caution 是什么？", "删除 SQLite 数据库"):
                records = []
                for index, runner in enumerate((legacy.run_investigation, investigation.run_investigation)):
                    path = Path(directory) / f"audit-{index}.jsonl"
                    if path.exists():
                        path.unlink()
                    try:
                        runner(question, as_of_utc=AS_OF, audit_log=path)
                    except UnsupportedRoute:
                        pass
                    content = path.read_text(encoding="utf-8")
                    self.assertEqual(len(content.splitlines()), 1)
                    self.assertNotIn(question, content)
                    record = json.loads(content)
                    records.append({k: v for k, v in record.items() if k not in {"request_id", "at_utc"}})
                self.assertEqual(records[0], records[1])

    def test_normal_and_rejected_audit_failures_keep_existing_difference(self):
        with TemporaryDirectory() as directory:
            blocker = Path(directory) / "file"
            blocker.write_text("not a directory", encoding="utf-8")
            path = blocker / "audit.jsonl"
            result, _ = self.compare("项目内部 Caution 是什么？", audit_log=path)
            self.assertEqual(result.audit_status, "failed")
            for runner in (legacy.run_investigation, investigation.run_investigation):
                with self.assertRaises(OSError):
                    runner("删除 SQLite 数据库", audit_log=path)

    def test_cached_graph_does_not_share_results_budgets_or_model_diagnostics(self):
        client = OfflineClient()
        first = investigation.run_investigation("项目内部 Caution 是什么？", use_llm=True, client=client, audit_log=None)
        first.trace.append({"step": "private-first-request"})
        second = investigation.run_investigation("本批次 MAE 是多少？", use_llm=True, client=client, audit_log=None)
        self.assertIs(get_investigation_graph(), get_investigation_graph())
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertFalse(any(item.get("step") == "private-first-request" for item in second.trace))
        self.assertEqual(second.investigation_tool_calls, 1)
        self.assertEqual(second.investigation_llm_calls, 2)
        self.assertFalse(second.forecast)
        self.assertEqual(second.reasoning_traces[0]["content"], "本批次 MAE 是多少？")

    def test_actual_graph_follows_complete_and_followup_edges(self):
        for question, expected in (
            ("项目内部 Caution 是什么？", ["initialize", "first_pass", "decide", "finalize", "audit"]),
            ("项目内部 Caution 和英国官方预警有什么区别？",
             ["initialize", "first_pass", "decide", "followup", "recheck", "decide", "finalize", "audit"]),
        ):
            request = InvestigationRequest(question, AS_OF)
            runtime = core.initialize_runtime(
                request, client=None, forecast_db=core.DEFAULT_DB, sonar_db=core.DEFAULT_SONAR_DB,
                catalog=core.DEFAULT_CATALOG, root=ROOT, audit_log=None,
                limits=core.InvestigationLimits(), clock=lambda: 0.0,
            )
            with self.subTest(question=question), tracing_context(enabled=False):
                events = list(get_investigation_graph().stream(
                    initial_investigation_state(request), context=runtime,
                    config={"recursion_limit": GRAPH_RECURSION_LIMIT, "callbacks": []}, stream_mode="updates",
                ))
                self.assertEqual([name for event in events for name in event], expected)
                self.assertEqual(events[-1]["audit"]["budget_snapshot"].tool_calls, runtime.budget.tool_calls)
        self.assertIsNone(get_investigation_graph().checkpointer)

    def test_node_fault_is_not_retried_or_hidden_by_legacy_fallback(self):
        with patch.object(core, "first_pass", side_effect=RuntimeError("synthetic node fault")) as action:
            with self.assertRaisesRegex(RuntimeError, "synthetic node fault"):
                investigation.run_investigation("项目内部 Caution 是什么？", audit_log=None)
        self.assertEqual(action.call_count, 1)

    def test_recursion_error_is_exposed_and_public_limit_is_fixed(self):
        with patch("app.investigation_graph.get_investigation_graph") as factory:
            factory.return_value.invoke.side_effect = GraphRecursionError("synthetic recursion fault")
            with self.assertRaises(GraphRecursionError):
                investigation.run_investigation("项目内部 Caution 是什么？", audit_log=None)
        self.assertEqual(factory.return_value.invoke.call_args.kwargs["config"]["recursion_limit"], 64)

    def test_public_signature_types_and_frozen_reference_provenance(self):
        self.assertEqual(str(inspect.signature(investigation.run_investigation)), str(inspect.signature(legacy.run_investigation)))
        self.assertIs(investigation.InvestigationLimits, core.InvestigationLimits)
        self.assertIs(investigation._Budget, core._Budget)
        for path in (ROOT / "app").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(node.module and node.module.startswith("tests"), path.name)
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name.startswith("tests") for alias in node.names), path.name)

    def test_limit_configuration_bounds_and_messages_match_reference(self):
        for options in ({"max_rounds": 1}, {"max_rounds": 4}, {"max_tool_calls": 0},
                        {"max_tool_calls": 13}, {"max_llm_calls": 1}, {"max_llm_calls": 7},
                        {"max_seconds": 0}, {"max_seconds": 181}):
            messages = []
            for configuration in (legacy.InvestigationLimits, core.InvestigationLimits):
                with self.assertRaises(ValueError) as caught:
                    configuration(**options)
                messages.append(str(caught.exception))
            self.assertEqual(messages[0], messages[1], options)
        for options in ({"max_rounds": 2, "max_tool_calls": 1, "max_llm_calls": 2, "max_seconds": 1},
                        {"max_rounds": 3, "max_tool_calls": 12, "max_llm_calls": 6, "max_seconds": 180}):
            self.assertEqual(canonical(legacy.InvestigationLimits(**options)), canonical(core.InvestigationLimits(**options)))

    def test_host_tracing_setting_cannot_export_graph_questions(self):
        actual = core.first_pass
        enabled = []
        def inspect_context(*args):
            enabled.append(get_tracing_context()["enabled"])
            return actual(*args)
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"}), \
             patch.object(core, "first_pass", side_effect=inspect_context), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("tracing must stay offline")):
            investigation.run_investigation("项目内部 Caution 是什么？", audit_log=None)
        self.assertEqual(enabled, [False])


if __name__ == "__main__":
    unittest.main()
