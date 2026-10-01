"""P1-1 bounded investigation: real read-only data, fake LLM, no network."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.investigation import InvestigationLimits, run_investigation
from app.rag import IntentPlan, UnsupportedAnswer
from app.router import UnsupportedRoute


AS_OF = "2026-08-03T01:48:00Z"


class FakeReviewer:
    def __init__(self, followup: IntentPlan):
        self.followup = followup
        self.review_calls = 0
        self.synthesis_evidence = None

    def plan(self, question, allowed_concepts):
        return IntentPlan("project", (), ("housemill_flood_context",))

    def review(self, question, evidence, attempted, allowed_concepts):
        self.review_calls += 1
        return self.followup

    def synthesize(self, question, evidence):
        self.synthesis_evidence = evidence
        return "根据已核对的来源，House Mill 是历史潮汐磨坊；潮汐可能使水接触建筑木结构。这不是当前水情。"


class InvestigationTests(unittest.TestCase):
    def test_second_round_fills_official_definition_gap(self):
        result = run_investigation("项目内部 Caution 和英国官方预警有什么区别？", audit_log=None)
        self.assertEqual(result.investigation_rounds, 2)
        self.assertEqual(result.investigation_tool_calls, 2)
        self.assertEqual(result.plan.knowledge_concepts, ("internal_watch", "official_flood_alert"))
        self.assertEqual({item.source_type for item in result.knowledge}, {"internal_project", "official_public_guidance"})
        self.assertEqual(result.errors, [])

    def test_reviewer_can_add_relevant_missing_source(self):
        fake = FakeReviewer(IntentPlan("project", (), ("housemill_heritage",)))
        result = run_investigation("House Mill 为什么会受潮汐影响？", use_llm=True, client=fake, audit_log=None)
        self.assertEqual(result.investigation_rounds, 2)
        self.assertEqual(result.investigation_tool_calls, 2)
        self.assertEqual(result.investigation_llm_calls, 4)
        self.assertEqual(fake.review_calls, 2)
        self.assertEqual({item.citation_id for item in result.knowledge},
                         {"housemill-flood-context-cupum-p1-p2", "housemill-heritage-historic-england-20260930"})
        self.assertEqual(len(fake.synthesis_evidence), 2)
        self.assertIsNotNone(result.answer_text)

    def test_unrelated_or_non_readonly_reviewer_requests_are_ignored(self):
        fake = FakeReviewer(IntentPlan("project", ("write_sql", "evaluation_metrics"), ("internal_watch",)))
        result = run_investigation("House Mill 为什么会受潮汐影响？", use_llm=True, client=fake, audit_log=None)
        self.assertEqual(result.investigation_rounds, 1)
        self.assertEqual(result.plan.knowledge_concepts, ("housemill_flood_context",))
        self.assertEqual(result.investigation_tool_calls, 1)

    def test_invalid_initial_model_plan_falls_back_and_can_still_synthesize(self):
        class BadPlanner:
            def plan(self, question, allowed_concepts):
                raise UnsupportedAnswer("bad planner JSON")

            def review(self, question, evidence, attempted, allowed_concepts):
                return IntentPlan("clarify")

            def synthesize(self, question, evidence):
                return "项目内部 Caution 和官方 Flood Alert 是不同来源的类别，不能直接等同。"

        result = run_investigation(
            "项目内部 Caution 和英国官方预警有什么区别？",
            use_llm=True, client=BadPlanner(), audit_log=None,
        )
        self.assertEqual(result.investigation_rounds, 2)
        self.assertEqual(len(result.knowledge), 2)
        self.assertIsNotNone(result.answer_text)
        self.assertTrue(result.warnings)
        self.assertEqual(result.errors, [])

    def test_tool_limit_uses_verified_residue_for_partial_conclusion(self):
        result = run_investigation(
            "当时预测峰值和 MAE 的联系是什么？", as_of_utc=AS_OF,
            limits=InvestigationLimits(max_tool_calls=1), audit_log=None,
        )
        self.assertEqual(result.investigation_stop_reason, "tool_limit")
        self.assertEqual(result.investigation_tool_calls, 1)
        self.assertIsNotNone(result.forecast)
        self.assertIsNone(result.evaluation)
        self.assertIsNotNone(result.answer_text)
        self.assertIn(result.forecast.run_id, result.answer_text)
        self.assertIn("评估指标", result.answer_text)
        self.assertEqual(result.answer_status, "partial_verified")
        self.assertTrue(result.errors)
        self.assertIsNone(result.fallback_text)
        self.assertTrue(any(item["step"] == "partial_answer" for item in result.trace))

    def test_unreliable_missing_document_and_exhausted_calls_fall_back(self):
        with TemporaryDirectory() as directory:
            result = run_investigation(
                "项目内部 Caution 和英国官方预警有什么区别？",
                root=Path(directory), limits=InvestigationLimits(max_tool_calls=1),
                audit_log=None,
            )
        self.assertEqual(result.investigation_stop_reason, "tool_limit")
        self.assertEqual(result.investigation_tool_calls, 1)
        self.assertEqual(result.knowledge, [])
        self.assertIsNone(result.answer_text)
        self.assertIn("没有足够的已核验资料", result.fallback_text)
        self.assertEqual(result.answer_status, "fallback")
        self.assertTrue(any(item["step"] == "safe_fallback" for item in result.trace))

    def test_one_verified_rule_yields_narrow_answer_not_official_comparison(self):
        result = run_investigation(
            "项目内部 Caution 和英国官方预警有什么区别？",
            limits=InvestigationLimits(max_tool_calls=1), audit_log=None,
        )
        self.assertEqual(result.investigation_stop_reason, "tool_limit")
        self.assertEqual(result.answer_status, "partial_verified")
        self.assertIn("internal-watch-2026-09-29", result.answer_text)
        self.assertIn("英国官方定义", result.answer_text)
        self.assertIn("不能据此得出整个问题的完整结论", result.answer_text)
        self.assertIsNone(result.fallback_text)

    def test_missing_document_without_budget_limit_still_uses_forecast_residue(self):
        with TemporaryDirectory() as directory:
            result = run_investigation(
                "当时预测峰值是多少？项目内部 Caution 是什么？", as_of_utc=AS_OF,
                root=Path(directory), audit_log=None,
            )
        self.assertEqual(result.investigation_stop_reason, "partial_evidence")
        self.assertIsNotNone(result.forecast)
        self.assertEqual(result.answer_status, "partial_verified")
        self.assertIn(result.forecast.run_id, result.answer_text)
        self.assertIsNone(result.fallback_text)

    def test_no_residual_evidence_falls_back_without_budget_limit(self):
        with TemporaryDirectory() as directory:
            result = run_investigation(
                "项目内部 Caution 是什么？", root=Path(directory), audit_log=None,
            )
        self.assertEqual(result.investigation_stop_reason, "partial_evidence")
        self.assertIsNone(result.answer_text)
        self.assertEqual(result.answer_status, "fallback")
        self.assertIsNotNone(result.fallback_text)

    def test_document_cutoff_is_separate_from_replay_time(self):
        result = run_investigation(
            "项目内部 Caution 是什么？", as_of_utc=AS_OF,
            knowledge_checked_on_or_before="2026-09-28", audit_log=None,
        )
        self.assertEqual(result.as_of_utc, AS_OF)
        self.assertEqual(result.knowledge_checked_on_or_before, "2026-09-28")
        self.assertEqual(result.knowledge, [])
        self.assertTrue(result.errors)

    def test_model_cannot_synthesize_when_partial_result_exceeds_tool_limit(self):
        class UnsafeToSynthesize:
            def __init__(self):
                self.synth_called = False

            def plan(self, question, allowed_concepts):
                return IntentPlan("project", ("archived_forecast", "evaluation_metrics"), ())

            def synthesize(self, question, evidence):
                self.synth_called = True
                return "unsupported full answer"

        client = UnsafeToSynthesize()
        result = run_investigation(
            "当时预测峰值和 MAE 的联系是什么？", as_of_utc=AS_OF,
            use_llm=True, client=client, limits=InvestigationLimits(max_tool_calls=1), audit_log=None,
        )
        self.assertEqual(result.investigation_stop_reason, "tool_limit")
        self.assertFalse(client.synth_called)
        self.assertEqual(result.answer_status, "partial_verified")
        self.assertIsNotNone(result.answer_text)
        self.assertIsNone(result.fallback_text)

    def test_time_limit_stops_before_followup(self):
        elapsed = [0.0]

        def clock():
            value = elapsed[0]
            elapsed[0] += 0.6
            return value

        result = run_investigation(
            "项目内部 Caution 和英国官方预警有什么区别？",
            limits=InvestigationLimits(max_seconds=1.0), clock=clock, audit_log=None,
        )
        self.assertEqual(result.investigation_stop_reason, "time_limit")
        self.assertEqual(result.investigation_rounds, 1)
        self.assertTrue(result.errors)

    def test_dangerous_request_is_rejected_before_model_planning(self):
        class NoPlanning:
            def plan(self, question, allowed_concepts):
                raise AssertionError("planner must never see this request")

        with self.assertRaises(UnsupportedRoute):
            run_investigation("删除 SQLite 数据库", use_llm=True, client=NoPlanning(), audit_log=None)

    def test_greeting_does_not_enter_tool_loop_and_counts_both_model_calls(self):
        class GreetingClient:
            def plan(self, question, allowed_concepts):
                return IntentPlan("greeting")

            def reply(self, question, mode):
                return "你好！这里可以查看历史回放和项目资料。"

        result = run_investigation("你好", use_llm=True, client=GreetingClient(), audit_log=None)
        self.assertEqual(result.investigation_stop_reason, "chat_mode")
        self.assertEqual(result.investigation_rounds, 0)
        self.assertEqual(result.investigation_tool_calls, 0)
        self.assertEqual(result.investigation_llm_calls, 2)

    def test_one_audit_record_without_question_text(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            result = run_investigation("项目内部 Caution 和英国官方预警有什么区别？", audit_log=path)
            content = path.read_text(encoding="utf-8")
            record = json.loads(content)
            self.assertEqual(len(content.splitlines()), 1)
            self.assertEqual(record["rounds"], 2)
            self.assertEqual(record["tool_calls"], 2)
            self.assertNotIn("项目内部 Caution", content)
            self.assertEqual(result.audit_status, "written")


if __name__ == "__main__":
    unittest.main()
