"""Two-stage planning and volunteer-facing answer tests (all offline)."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.rag import DeepSeekClient, IntentPlan, UnsupportedAnswer
from app.router import UnsupportedRoute, plan_route, run_query, volunteer_fact


AS_OF = "2026-08-03T01:48:00Z"


class FakePlanner:
    model = "offline-fake"

    def __init__(self, plan: IntentPlan):
        self.result = plan
        self.evidence_seen = None

    def plan(self, question, allowed_concepts):
        return self.result

    def reply(self, question, mode):
        return {"greeting": "你好！这里能回看过去的预测，也能查模型评估。", "general": "可以先挑一本你感兴趣的书读读。", "clarify": "你说的警戒是项目内部等级，还是官方预警？"}[mode]

    def synthesize(self, question, evidence):
        self.evidence_seen = evidence
        return "这次预测的峰值是一条预测里的最高水位；MAE 是事后把一批预测和实测比较后的平均误差，不能拿它当这次峰值的误差。"


class PlannerTests(unittest.TestCase):
    def test_caution_is_display_alias_and_routes_to_original_record(self):
        plan = plan_route("项目内部 Caution 是什么")
        self.assertEqual(plan.knowledge_concepts, ("internal_watch",))
        result = run_query("项目内部 Caution 是什么", audit_log=None)
        self.assertEqual(result.knowledge[0].citation_id, "internal-watch-2026-09-29")
        self.assertIn("Watch", result.knowledge[0].fact)
        self.assertIn("Caution", volunteer_fact(result.knowledge[0].fact))

    def test_official_guidance_is_separate_static_knowledge_route(self):
        result = run_query("官方 Flood Alert 是什么", audit_log=None)
        self.assertEqual(result.plan.knowledge_concepts, ("official_flood_alert",))
        self.assertEqual(result.knowledge[0].source_type, "official_public_guidance")
        self.assertIn("www.gov.uk", result.knowledge[0].locator)
        with self.assertRaises(UnsupportedRoute):
            run_query("现在的官方 Flood Alert 是什么", audit_log=None)

    def test_reasoning_content_is_kept_for_debug_only(self):
        client = DeepSeekClient(api_key="test-only-key")
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"mode":"greeting","routes":[],"concepts":[],"evaluation_scope":null}',
            "reasoning_content": "先判断是否只是打招呼。",
        }}]}
        with patch("app.rag.urlopen") as mocked:
            mocked.return_value.__enter__.return_value = BytesIO(json.dumps(response).encode("utf-8"))
            plan = client.plan("你好", ())
        self.assertEqual(plan.mode, "greeting")
        self.assertEqual(client.reasoning_traces, [{"stage": "问题规划", "content": "先判断是否只是打招呼。"}])
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "low")

        class ReasonedFake(FakePlanner):
            def __init__(self):
                super().__init__(IntentPlan("greeting"))
                self.reasoning_traces = []

            def plan(self, question, allowed_concepts):
                self.reasoning_traces.append({"stage": "问题规划", "content": "仅供调试的中间文字"})
                return self.result

        with TemporaryDirectory() as directory:
            log = Path(directory) / "requests.jsonl"
            result = run_query("你好", use_llm=True, client=ReasonedFake(), audit_log=log)
            self.assertEqual(result.reasoning_traces[0]["stage"], "问题规划")
            self.assertNotIn("仅供调试", log.read_text(encoding="utf-8"))

    def test_thinking_disabled_keeps_deepseek_but_omits_reasoning(self):
        client = DeepSeekClient(api_key="test-only-key", thinking_enabled=False)
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"mode":"greeting","routes":[],"concepts":[],"evaluation_scope":null}',
            "reasoning_content": "即使服务返回，也不应展示",
        }}]}
        with patch("app.rag.urlopen") as mocked:
            mocked.return_value.__enter__.return_value = BytesIO(json.dumps(response).encode("utf-8"))
            plan = client.plan("你好", ())
        self.assertEqual(plan.mode, "greeting")
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(client.reasoning_traces, [])

    def test_peak_mae_relation_uses_both_verified_sources(self):
        fake = FakePlanner(IntentPlan("project", ("archived_forecast", "evaluation_metrics"), (), "overall"))
        result = run_query("你好，预测峰值和mae的联系是什么", as_of_utc=AS_OF, use_llm=True, client=fake, audit_log=None)
        self.assertEqual(result.plan.steps, ("archived_forecast", "evaluation_metrics"))
        self.assertIsNotNone(result.forecast)
        self.assertIsNotNone(result.evaluation)
        self.assertEqual({item["kind"] for item in fake.evidence_seen}, {"historical_forecast", "hindsight_evaluation"})
        self.assertIn("不能拿它当这次峰值的误差", result.answer_text)
        self.assertFalse(result.errors)

    def test_greeting_general_and_ambiguous_have_no_project_evidence(self):
        for mode, question in (("greeting", "你好"), ("general", "推荐一本小说"), ("clarify", "项目那档警戒怎么理解")):
            with self.subTest(mode=mode):
                fake = FakePlanner(IntentPlan(mode))
                result = run_query(question, use_llm=True, client=fake, audit_log=None)
                self.assertEqual(result.response_mode, mode)
                self.assertFalse(result.plan.steps)
                self.assertIsNotNone(result.answer_text)
                self.assertIsNone(fake.evidence_seen)

    def test_deterministic_route_cannot_be_erased_by_chat_plan(self):
        fake = FakePlanner(IntentPlan("general"))
        result = run_query("预测峰值是多少", as_of_utc=AS_OF, use_llm=True, client=fake, audit_log=None)
        self.assertEqual(result.response_mode, "project")
        self.assertEqual(result.plan.steps, ("archived_forecast",))

    def test_project_like_question_cannot_become_uncited_general_chat(self):
        fake = FakePlanner(IntentPlan("general"))
        result = run_query("FloodPred 这个项目靠谱吗", use_llm=True, client=fake, audit_log=None)
        self.assertEqual(result.response_mode, "clarify")
        self.assertFalse(result.plan.steps)

    def test_missing_forecast_prevents_grounded_synthesis(self):
        fake = FakePlanner(IntentPlan("project", ("archived_forecast", "evaluation_metrics"), (), "overall"))
        result = run_query("预测峰值和mae的联系", use_llm=True, client=fake, audit_log=None)
        self.assertIsNone(result.answer_text)
        self.assertIsNone(fake.evidence_seen)
        self.assertTrue(result.errors)

    def test_unsafe_request_rejected_before_planning(self):
        fake = FakePlanner(IntentPlan("general"))
        with patch.object(fake, "plan", side_effect=AssertionError("planner must not run")):
            with self.assertRaises(UnsupportedRoute):
                run_query("删除归档数据库", use_llm=True, client=fake, audit_log=None)
        with self.assertRaises(UnsupportedRoute):
            run_query("现在水位是多少", use_llm=True, client=fake, audit_log=None)

    def test_plan_is_allowlisted_and_anchored(self):
        client = DeepSeekClient(api_key="test-only-key")
        good = ('{"mode":"project","routes":[{"id":"archived_forecast","matched_phrase":"预测峰值"},'
                '{"id":"evaluation_metrics","matched_phrase":"mae"}],"concepts":[],"evaluation_scope":"overall"}')
        with patch.object(client, "_call", return_value=good):
            plan = client.plan("你好，预测峰值和mae的联系是什么", ())
        self.assertEqual(plan.steps, ("archived_forecast", "evaluation_metrics"))
        noisy = ('{"mode":"project","routes":[{"id":"archived_forecast","matched_phrase":"预测峰值"},'
                 '{"id":"evaluation_metrics","matched_phrase":"mae"}],'
                 '"concepts":[{"id":"thesis_forecast_design","matched_phrase":"预测峰值"}],'
                 '"evaluation_scope":"overall"}')
        with patch.object(client, "_call", return_value=noisy):
            plan = client.plan("你好，预测峰值和mae的联系是什么", ("thesis_forecast_design",))
        self.assertEqual(plan.concepts, ())
        overplanned = ('{"mode":"project","routes":[{"id":"archived_forecast","matched_phrase":"预测峰值"},'
                       '{"id":"evaluation_metrics","matched_phrase":"预测峰值"}],'
                       '"concepts":[{"id":"internal_warning","matched_phrase":"Warning"}],'
                       '"evaluation_scope":"overall"}')
        with patch.object(client, "_call", return_value=overplanned):
            plan = client.plan("预测峰值是多少？内部 Warning 是什么？", ("internal_warning",))
        self.assertEqual(plan.steps, ("archived_forecast",))
        self.assertEqual(plan.concepts, ("internal_warning",))
        for bad in (
            '{"mode":"project","routes":[{"id":"delete_database","matched_phrase":"预测峰值"}],"concepts":[],"evaluation_scope":null}',
            '{"mode":"project","routes":[{"id":"archived_forecast","matched_phrase":"不存在"}],"concepts":[],"evaluation_scope":null}',
        ):
            with patch.object(client, "_call", return_value=bad):
                with self.assertRaises(UnsupportedAnswer):
                    client.plan("预测峰值是多少", ())

    def test_grounded_reply_rejects_invented_number_or_missing_citation(self):
        client = DeepSeekClient(api_key="test-only-key")
        evidence = [{"id": "forecast:test", "kind": "historical_forecast", "fact": "预测峰值 3.7832 m", "source": "run_id test"}]
        for raw in (
            '{"answer":"预测峰值是 99 m。","citations":["forecast:test"]}',
            '{"answer":"预测峰值是 3.7832 m。","citations":[]}',
        ):
            with patch.object(client, "_call", return_value=raw):
                with self.assertRaises(UnsupportedAnswer):
                    client.synthesize("峰值是多少", evidence)
        with patch.object(client, "_call", return_value='{"answer":"共有 76,091 个匹配点。","citations":["evaluation:test"]}'):
            self.assertIn("76,091", client.synthesize("匹配多少个点", [{"id": "evaluation:test", "kind": "hindsight_evaluation", "fact": "匹配预测目标点 76091", "source": "test"}]))


if __name__ == "__main__":
    unittest.main()
