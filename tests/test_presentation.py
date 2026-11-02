"""Presentation-only references, metadata isolation and rerender behavior."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
import os
import tempfile
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.evidence_gate import Citation, QuestionEvidence, VerifiedClaim
from app.investigation import run_investigation
from app.presentation import ResultPresentation
from app.router import QueryResult, RoutePlan


ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-08-03T01:48:00Z"


def visible(app):
    values = []
    for kind in ("markdown", "caption", "code", "warning", "error", "info", "json", "table", "metric"):
        values.extend(str(item.value) for item in app.get(kind))
    values.extend(item.label for item in app.expander)
    return "\n".join(values)


class PresentationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"FLOODPRED_CHAT_DB": str(Path(tmp.name) / "chat.sqlite3")})
        env.start()
        self.addCleanup(env.stop)
    @classmethod
    def setUpClass(cls):
        cls.result = run_investigation(
            "当时预测峰值是多少？项目内部 Caution 是什么？",
            as_of_utc=AS_OF, use_llm=False, audit_log=None,
        )

    def test_real_forecast_display_preserves_facts_without_mutating_result(self):
        original = deepcopy(self.result)
        text = ResultPresentation(self.result).text(self.result.answer_text)
        for technical in ("run_id", self.result.forecast.run_id, "q1-tool", "forecast:"):
            self.assertNotIn(technical, text)
        for fact in ("3.7832 m", "2026-08-03T16:15:00Z", "4.20 m", "4.43 m", "来源 1", "来源 2"):
            self.assertIn(fact, text)
        self.assertEqual(self.result, original)

    def test_product_boilerplate_is_hidden_but_evidence_limits_remain(self):
        display = ResultPresentation(self.result)
        original = self.result.answer_text
        self.assertIn("这是内部等级，不是官方警报。", original)
        self.assertNotIn("这是内部等级，不是官方警报。", display.text(original))
        self.assertNotIn("不是实测或实时水情", display.text(original))
        other = ("这是内部规则，不代表官方发布了警报。这里不是实时水情或官方警报。"
                 "这里说的是预测方法，不是当前水情。这是建筑背景，不是当前洪水状态。"
                 "这是历史研究背景，不是今天的情况。")
        self.assertEqual(display.text(other), "")
        important = "来源有冲突。尚不能确认 MAE：缺少原文。因此不能据此得出整个问题的完整结论。请求失败：TimeoutError。"
        self.assertEqual(display.text(important), important)
        self.assertEqual(self.result.answer_text, original)

    def test_shared_source_versions_get_stable_numbers(self):
        a = Citation("a", "C:/sources/report.md:1-3 § A", "2026-09-29", "thesis", "原文 A")
        b = replace(a, id="b", locator="C:/sources/report.md:8-9 § B", quote="原文 B")
        c = replace(a, id="c", version="2026-10-28")
        result = QueryResult("id", "问题", None, RoutePlan((), (), None),
            verified_claims=[VerifiedClaim("q1-1", "q1", "甲", (a,)),
                             VerifiedClaim("q2-1", "q2", "乙", (b, a, c))])
        display = ResultPresentation(result)
        self.assertEqual(display.numbers, {"a": 1, "b": 1, "c": 2})
        self.assertEqual(display.reference((b, a, c)), "〔来源 1、2〕")
        self.assertEqual(ResultPresentation(result).numbers, display.numbers)

    def test_only_known_metadata_is_replaced_and_gaps_remain(self):
        result = deepcopy(self.result)
        result.question_evidence.append(QuestionEvidence("q3", "MAE 是多少", "value"))
        unknown = "abcdef01" * 8
        text = ResultPresentation(result).text(f"尚不能确认 q3‘MAE 是多少’：缺少原文。正常内容 {unknown}；3.14 m。")
        self.assertIn("问题 3", text)
        self.assertIn("缺少原文", text)
        self.assertIn(unknown, text)
        self.assertIn("3.14 m", text)
        self.assertNotIn("来源", text)  # no invented citation for an unsupported question

    def test_plain_debug_plain_keeps_answer_and_calls_investigator_once(self):
        result = deepcopy(self.result)
        result.reasoning_traces = [{"stage": "规划", "content": "模型诊断文字"}]
        before = deepcopy(result)
        with patch("app.investigation.run_investigation", return_value=result) as run:
            app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()
            self.assertFalse(app.toggle[0].value)
            self.assertTrue(app.checkbox(key='use_thinking').value)
            app.checkbox(key='use_llm').set_value(False)
            app.chat_input[0].set_value(app.chat_input[0].value or "当时预测峰值是多少？项目内部 Caution 是什么？").run()
            self.assertFalse(app.exception)
            normal = visible(app)
            self.assertNotIn("这是内部等级，不是官方警报。", normal)
            self.assertNotIn("内部等级不等于英国官方预警", normal)
            self.assertNotIn("这是当时的预测，不是实测。", normal)
            for technical in ("run_id", result.forecast.run_id, "q1-tool", "forecast:", "SHA-256", str(ROOT), "模型诊断文字"):
                self.assertNotIn(technical, normal)
            for claim in result.verified_claims:
                for citation in claim.citations:
                    self.assertNotIn(citation.locator, normal)
                    if citation.sha256:
                        self.assertNotIn(citation.sha256, normal)
            for fact in ("3.7832 m", "2026-08-03T16:15:00Z", "来源 1", "4.20m <= level < 4.43m"):
                self.assertIn(fact, normal)
            app.toggle[0].set_value(True).run()
            self.assertFalse(app.exception)
            debug = visible(app)
            self.assertIn("这是内部等级，不是官方警报。", debug)  # original answer, kept for diagnosis
            for technical in ("run_id", result.forecast.run_id, "q1-tool", "forecast:", "SHA-256", str(ROOT), "source_mapping", "question_graph"):
                self.assertIn(technical, debug)
            self.assertEqual([tab.label for tab in app.tabs], ["回答", "证据", "运行细节"])
            app.toggle[0].set_value(False).run()
            self.assertEqual(visible(app), normal)
            self.assertFalse(app.get("json"))
            self.assertEqual([tab.label for tab in app.tabs], ["回答", "证据"])
            run.assert_called_once()
        self.assertEqual(result, before)

    def test_failed_new_question_clears_cached_answer_even_on_unexpected_error(self):
        with patch("app.investigation.run_investigation", side_effect=[self.result, RuntimeError("模拟失败")]) as run:
            app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()
            app.chat_input[0].set_value(app.chat_input[0].value or "当时预测峰值是多少？项目内部 Caution 是什么？").run()
            self.assertTrue(app.metric)
            app.chat_input[0].set_value("2026-08-03T01:48:00Z 重新查一下预测峰值")
            app.chat_input[0].set_value(app.chat_input[0].value or "当时预测峰值是多少？项目内部 Caution 是什么？").run()
            self.assertFalse(app.exception)
            self.assertTrue(any('查询未完成' in item.value for item in app.markdown))
            self.assertNotIn('3.7832', app.chat_message[-1].markdown[0].value)
            app.toggle[0].set_value(True).run()
            self.assertTrue(app.metric)  # Previous successful turn remains historical.
            self.assertTrue(app.tabs)
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
