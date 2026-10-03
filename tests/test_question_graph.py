"""Annotated requests: graph coverage, qualifiers, model and tool boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.investigation import run_investigation
from app.question_graph import (GraphValidationError, build_question_graph,
                                checked_question_graph)
from app.rag import DeepSeekClient, IntentPlan, UnsupportedAnswer
from app.router import run_query


def proposed_node(item_id, phrase, subjects, kind="fact", dependencies=()):
    return {"id": item_id, "matched_phrase": phrase, "subjects": subjects,
            "kind": kind, "depends_on": list(dependencies)}


class QuestionGraphTests(unittest.TestCase):
    def test_single_request_keeps_exact_raw_span(self):
        question = "  当时预测峰值是多少？  "
        graph = build_question_graph(question)
        self.assertEqual(graph.original_text, question)
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes[0].requested_attribute, "value")
        self.assertEqual(graph.nodes[0].temporal_scope, "historical")
        for node in graph.nodes:
            for span in (*node.anchors, *node.subjects, *node.qualifiers):
                self.assertEqual(question[span.start:span.end], span.text)

    def test_greeting_does_not_create_fact(self):
        self.assertFalse(build_question_graph("你好！").nodes)
        graph = build_question_graph("你好，项目内部 Caution 是什么？")
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes[0].source_scope, "internal")

    def test_two_independent_questions_are_not_forced_into_relation(self):
        graph = build_question_graph("当时预测峰值是多少？内部 Warning 是什么？")
        self.assertEqual(len(graph.nodes), 2)
        self.assertFalse(any(node.depends_on for node in graph.nodes))

    def test_relation_has_two_endpoints_not_fabricated_value_request(self):
        graph = build_question_graph("你好，预测峰值和 MAE 的联系是什么？")
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual([node.requested_attribute for node in graph.nodes],
                         ["meaning_and_scope", "meaning_and_scope", "connection"])
        self.assertEqual(graph.nodes[-1].depends_on, ("q1", "q2"))
        self.assertIn("aggregate_mae_is_not_single_peak_error", graph.nodes[-1].evidence_requirements)
        self.assertEqual(graph.nodes[1].temporal_scope, "conceptual")

    def test_historical_peak_does_not_make_mae_historically_known(self):
        graph = build_question_graph("当时预测峰值和 MAE 的联系是什么？")
        self.assertNotEqual(graph.nodes[1].temporal_scope, "historical")
        self.assertIn("hindsight_not_available_at_replay", graph.nodes[1].evidence_requirements)

    def test_pronoun_resolves_but_current_forecast_is_clarified(self):
        question = "你好，旧传感器是怎么布置的？它和现在的预测有什么关系？"
        graph = build_question_graph(question)
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(graph.coreferences[0].status, "resolved")
        self.assertEqual(graph.coreferences[0].candidates[0].text, "旧传感器")
        self.assertEqual(graph.nodes[0].requested_attribute, "placement")
        self.assertEqual(graph.nodes[-1].depends_on, ("q1", "q2"))
        self.assertTrue(graph.needs_clarification)
        with patch("app.router.search", side_effect=AssertionError("must not retrieve")):
            result = run_investigation(question, audit_log=None)
        self.assertEqual(result.response_mode, "clarify")
        self.assertEqual(result.investigation_tool_calls, 0)
        self.assertEqual(result.investigation_stop_reason, "needs_clarification")

    def test_explicit_project_method_resolves_temporal_ambiguity(self):
        graph = build_question_graph("旧传感器怎么布置？它和现在的 FloodPred 预测方法有什么关系？")
        self.assertFalse(graph.needs_clarification)
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(graph.nodes[1].temporal_scope, "current_project")

    def test_two_antecedents_must_not_use_nearest_guess(self):
        graph = build_question_graph("旧传感器和新传感器怎么布置？它在哪里？")
        self.assertEqual(graph.coreferences[0].status, "ambiguous")
        self.assertEqual(len(graph.coreferences[0].candidates), 2)
        self.assertTrue(graph.needs_clarification)

    def test_negation_and_authority_are_retained(self):
        graph = build_question_graph("项目内部 Caution 不是官方 Flood Alert，对吗？")
        qualifiers = {span.text for node in graph.nodes for span in node.qualifiers}
        self.assertTrue({"内部", "不是", "官方"} <= qualifiers)
        self.assertEqual([node.source_scope for node in graph.nodes[:2]], ["internal", "official"])
        self.assertEqual(graph.nodes[-1].source_scope, "mixed")

    def test_decimal_does_not_split_and_date_remains_anchored(self):
        question = "2026-08-03 当时水位是否达到 4.43m？"
        graph = build_question_graph(question)
        self.assertEqual(len(graph.nodes), 1)
        self.assertIn("4.43m", graph.nodes[0].anchors[0].text)
        self.assertIn("2026-08-03", {span.text for span in graph.nodes[0].qualifiers})

    def test_fullwidth_and_case_normalization_keeps_original(self):
        graph = build_question_graph("你好，ＭＡＥ是多少？")
        self.assertEqual(graph.original_text, "你好，ＭＡＥ是多少？")
        self.assertIn("mae", graph.normalized_text)
        self.assertEqual(graph.nodes[0].subjects[0].text, "ＭＡＥ")
        self.assertIn("unit", graph.nodes[0].evidence_requirements)

    def test_duplicate_question_is_merged_with_both_anchors(self):
        graph = build_question_graph("MAE是多少？MAE是多少？")
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(len(graph.nodes[0].anchors), 2)

    def test_number_comparison_is_not_missing_an_endpoint(self):
        graph = build_question_graph("论文中的 136 次与 42 次有什么区别？")
        self.assertFalse(graph.needs_clarification)
        self.assertEqual([span.text for span in graph.nodes[-1].subjects], ["136 次", "42 次"])
        self.assertEqual(graph.nodes[-1].question_type, "comparison")

    def test_explicit_exclusion_suppresses_metric_tool(self):
        with patch("app.router.evaluation_metrics_tool", side_effect=AssertionError("must not call")):
            result = run_query("不要查 MAE，只看当时预测峰值是多少", as_of_utc="2026-08-03T01:48:00Z", audit_log=None)
        self.assertEqual(result.plan.steps, ("archived_forecast",))
        self.assertIsNone(result.evaluation)

    def test_explicit_exclusion_suppresses_document_tool(self):
        result = run_query("不要查内部 Warning，只看当时预测峰值是多少", as_of_utc="2026-08-03T01:48:00Z", audit_log=None)
        self.assertEqual(result.plan.steps, ("archived_forecast",))
        self.assertFalse(result.knowledge)

    def test_valid_model_refines_fuzzy_subject_extraction(self):
        question = "以前那些测水的东西怎么布置的？"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", "以前那些测水的东西怎么布置的", ["那些测水的东西"])]})
        self.assertEqual(graph.origin, "deepseek_checked")
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes[0].requested_attribute, "placement")
        self.assertIn("以前", {span.text for span in graph.nodes[0].qualifiers})

    def test_coordinated_commands_keep_exclusion_local(self):
        with patch("app.router.evaluation_metrics_tool", side_effect=AssertionError("must not call")):
            result = run_query("不要查MAE只看当时预测峰值是多少", as_of_utc="2026-08-03T01:48:00Z", audit_log=None)
        self.assertEqual(result.plan.steps, ("archived_forecast",))
        graph = build_question_graph("预测峰值是多少并解释内部Warning是什么")
        self.assertEqual([node.requested_attribute for node in graph.nodes], ["value", "meaning_and_scope"])

    def test_valid_model_relation_keeps_acyclic_endpoint_links(self):
        question = "预测峰值和MAE有什么联系"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", question, ["预测峰值"], "definition"),
            proposed_node("q2", question, ["MAE"], "definition"),
            proposed_node("q3", question, ["预测峰值", "MAE"], "relation", ("q1", "q2"))]})
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(graph.nodes[-1].depends_on, ("q1", "q2"))

    def test_model_method_fragment_does_not_duplicate_relationship_endpoint(self):
        question = "旧传感器怎么布置？它和FloodPred预测方法有什么关系？"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", "FloodPred预测方法", ["FloodPred预测方法"], "fact")]})
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(graph.nodes[1].requested_attribute, "method")

    def test_narrow_model_fragment_inherits_parent_time_and_source(self):
        question = "现在的 FloodPred预测方法是什么"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", "FloodPred预测方法", ["FloodPred预测方法"], "fact")]})
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes[0].temporal_scope, "current_project")
        self.assertIn("现在", {span.text for span in graph.nodes[0].qualifiers})

    def test_excluding_all_available_requests_has_concrete_clarification(self):
        result = run_investigation("不要查MAE", audit_log=None)
        self.assertEqual(result.response_mode, "clarify")
        self.assertEqual(result.investigation_tool_calls, 0)
        self.assertIn("改查哪一部分", result.answer_text)

    def test_mentioned_exclusion_is_not_an_execution_instruction(self):
        graph = build_question_graph("别人说‘不要查MAE’是什么意思？")
        self.assertFalse(any(node.excluded for node in graph.nodes))

    def test_double_negative_requires_clarification(self):
        with patch("app.router.evaluation_metrics_tool", side_effect=AssertionError("must not call")):
            result = run_query("我不是不要查MAE", audit_log=None)
        self.assertEqual(result.response_mode, "clarify")
        self.assertIn("多层否定", result.answer_text)

    def test_graph_for_another_request_cannot_add_topics(self):
        class WrongGraphClient:
            def plan(self, question, allowed_concepts):
                return IntentPlan("project", (), ("internal_warning",), None, build_question_graph("另一个问题"))
        result = run_query("项目内部 Caution 是什么", use_llm=True, client=WrongGraphClient(),
                           generate_answer=False, audit_log=None)
        self.assertEqual(result.plan.knowledge_concepts, ("internal_watch",))
        self.assertEqual(result.question_graph.original_text, result.question)
        self.assertTrue(result.errors)

    def test_overlong_graph_clarifies_instead_of_executing_truncated_plan(self):
        graph = build_question_graph("旧传感器在哪里？MAE是多少？内部Warning是什么？House Mill是什么？PatchTST怎么设计？当时水位是多少？预测峰值是多少？")
        self.assertEqual(len(graph.nodes), 6)
        self.assertTrue(graph.needs_clarification)

    def test_model_cannot_erase_negative_or_source_qualifiers(self):
        question = "内部 Caution 不是官方 Flood Alert"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", question, ["Caution"], "definition")]})
        self.assertIn("不是", {span.text for node in graph.nodes for span in node.qualifiers})
        self.assertTrue(any("Flood Alert" in [span.text for span in node.subjects] for node in graph.nodes))

    def test_model_cannot_invent_subject_date_or_value(self):
        question = "旧传感器怎么布置？"
        for phrase, subjects in (("旧传感器怎么布置", ["LoRa"]),
                                 ("2025-01-01 的旧传感器", ["旧传感器"]),
                                 ("旧传感器怎么布置", ["4.70m"])):
            with self.subTest(subjects=subjects), self.assertRaises(GraphValidationError):
                checked_question_graph(question, {"nodes": [proposed_node("q1", phrase, subjects)]})

    def test_model_cannot_add_execution_fields_or_cycles(self):
        question = "MAE是多少"
        bad = proposed_node("q1", question, ["MAE"], dependencies=("q1",))
        for proposal in ({"nodes": [bad]}, {"nodes": [proposed_node("q1", question, ["MAE"])], "sql": "DELETE"},
                         {"nodes": [proposed_node("q1", question, ["MAE"], dependencies=({},))]}):
            with self.assertRaises(GraphValidationError):
                checked_question_graph(question, proposal)

    def test_model_cannot_force_false_relation_or_hide_ambiguity(self):
        with self.assertRaises(GraphValidationError):
            checked_question_graph("MAE是多少", {"nodes": [
                proposed_node("q1", "MAE是多少", ["MAE"], "relation")]})
        graph = checked_question_graph("旧传感器和新传感器怎么布置？它在哪里？", {"nodes": []})
        self.assertTrue(graph.needs_clarification)

    def test_model_can_expand_only_verified_coreference(self):
        question = "旧传感器怎么布置？它和FloodPred预测方法有什么关系？"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", "旧传感器怎么布置", ["旧传感器"]),
            proposed_node("q2", "FloodPred预测方法", ["FloodPred预测方法"], "definition"),
            proposed_node("q3", "它和FloodPred预测方法有什么关系", ["旧传感器", "FloodPred预测方法"], "relation", ("q1", "q2"))]})
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(graph.coreferences[0].status, "resolved")
        with self.assertRaises(GraphValidationError):
            checked_question_graph("旧传感器和新传感器怎么布置？它在哪里？", {"nodes": [
                proposed_node("q1", "它在哪里", ["旧传感器"])]})

    def test_model_spacing_normalization_still_has_exact_raw_positions(self):
        question = "ＭＡＥ和预测峰值是什么关系"
        graph = checked_question_graph(question, {"nodes": [
            proposed_node("q1", "mae 和 预测峰值 是 什么关系", ["mae"], "definition")]})
        for node in graph.nodes:
            for span in (*node.anchors, *node.subjects, *node.qualifiers):
                self.assertEqual(question[span.start:span.end], span.text)

    def test_invalid_model_graph_falls_back_to_local_plan(self):
        question = "项目内部 Caution 是什么"
        client = DeepSeekClient(api_key="test-only-key")
        raw = json.dumps({"mode": "project", "routes": [], "evaluation_scope": None,
                          "concepts": [{"id": "internal_watch", "matched_phrase": "Caution"}],
                          "question_graph": {"nodes": [proposed_node("q1", question, ["LoRa"])]}})
        with patch.object(client, "_call", return_value=raw):
            with self.assertRaises(UnsupportedAnswer):
                client.plan(question, ("internal_watch",))
            result = run_query(question, use_llm=True, client=client, generate_answer=False, audit_log=None)
        self.assertEqual(result.question_graph.origin, "local")
        self.assertEqual(result.plan.knowledge_concepts, ("internal_watch",))
        self.assertTrue(result.errors)

    def test_audit_contains_counts_not_raw_graph(self):
        with TemporaryDirectory() as directory:
            log = Path(directory) / "requests.jsonl"
            result = run_query("不要查MAE，只看当时预测峰值是多少", as_of_utc="2026-08-03T01:48:00Z", audit_log=log)
            record = json.loads(log.read_text(encoding="utf-8"))
        self.assertTrue(result.question_graph)
        self.assertNotIn("不要查MAE", json.dumps(record, ensure_ascii=False))
        self.assertTrue(any(step["step"] == "question_graph" for step in record["trace"]))


if __name__ == "__main__":
    unittest.main()
