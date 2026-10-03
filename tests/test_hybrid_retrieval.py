"""Step 2: candidate recall and provenance, never answer-entailment scores."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.hybrid_retrieval import (document_requests, hybrid_search, make_request,
                                  candidate_key, reciprocal_rank_fusion, request_for_concept, safe_trace)
from app.knowledge import (DEFAULT_CATALOG, ROOT, NoEvidence, SourceIntegrityError,
                           VersionConflict)
from app.question_graph import SourceSpan, build_question_graph
from app.router import run_query
from app.investigation import run_investigation


class HybridRetrievalTests(unittest.TestCase):
    def test_original_and_fragment_are_retained_verbatim(self):
        question = "你好，旧水位设备的安装点在哪里？不是问现在。"
        graph = build_question_graph(question)
        node = graph.nodes[0]
        request = make_request(question, fragment=node.anchors[0].text, node=node,
                               supplementary="House Mill旧传感器")
        self.assertEqual(request.queries[0].text, question)
        self.assertEqual(request.fragment, node.anchors[0].text)
        self.assertIn("不是问现在", request.original)
        self.assertLessEqual(sum(q.role == "rewrite" for q in request.queries), 2)
        self.assertLess(next(q.weight for q in request.queries if q.role == "supplementary"),
                        next(q.weight for q in request.queries if q.role == "original"))

    def test_identical_original_fragment_queries_are_deduplicated(self):
        request = make_request("旧传感器怎么布置")
        self.assertEqual(len(request.queries), 1)
        self.assertEqual(request.queries[0].role, "original+fragment")

    def test_invented_fragment_and_rewrite_subject_are_rejected(self):
        with self.assertRaises(ValueError):
            make_request("旧传感器怎么布置", fragment="新传感器在哪里")
        graph = build_question_graph("旧传感器怎么布置")
        invented = replace(graph.nodes[0], subjects=(SourceSpan(0, 4, "新传感器"),))
        with self.assertRaises(ValueError):
            make_request(graph.original_text, node=invented)

    def test_old_does_not_hard_filter_to_prior_project(self):
        graph = build_question_graph("旧传感器怎么布置")
        request = make_request(graph.original_text, node=graph.nodes[0])
        self.assertIsNone(request.source_types)

    def test_paper_qualifier_allows_both_research_and_thesis(self):
        self.assertEqual(make_request("论文中的水位预测方法").source_types, ("thesis", "research_paper"))
        self.assertEqual(make_request("毕业论文中的内部 Caution").source_types, ("thesis",))
        self.assertEqual(make_request("Wilson论文中的潮汐影响").source_types, ("research_paper",))
        self.assertEqual(make_request("GitHub中旧传感器怎么布置").source_types, ("prior_project",))
        self.assertEqual(make_request("不是论文里的Caution，问项目内部Caution").source_types, ("internal_project",))
        self.assertIsNone(make_request("不是官方资料里的洪水背景").source_types)

    def test_unqualified_sensor_query_recalls_multiple_source_types(self):
        result = hybrid_search(make_request("旧水位设备的安装点在哪里"))
        self.assertEqual(result.hits[0].record["concept"], "housemill_old_sensor")
        types = {hit.record["source_type"] for hit in result.hits}
        self.assertIn("prior_project", types)
        self.assertIn("thesis", types)
        self.assertEqual(result.to_debug()["support_status"], "candidate_only")

    def test_explicit_source_version_and_date_filter_before_ranking(self):
        request = make_request("项目内部 Caution 是什么")
        result = hybrid_search(request, version="source-snapshot-2026-09-29",
                               checked_on_or_before="2026-09-29")
        self.assertTrue(all(hit.record["source_type"] == "internal_project" for hit in result.hits))
        self.assertTrue(all(hit.record["version"] == "source-snapshot-2026-09-29" for hit in result.hits))
        with self.assertRaises(NoEvidence):
            hybrid_search(request, checked_on_or_before="2026-09-28")
        with self.assertRaises(ValueError):
            hybrid_search(request, checked_on_or_before="yesterday")

    def test_rrf_uses_ranks_not_raw_score_magnitude(self):
        a = [("raw", "keyword", 1.0, [("one", 1.0), ("two", 0.2)]),
             ("raw", "semantic_candidate", 1.0, [("two", 0.5), ("one", 0.4)])]
        b = [("raw", "keyword", 1.0, [("one", 1000000.0), ("two", 200000.0)]), a[1]]
        first, trace = reciprocal_rank_fusion(a)
        second, _ = reciprocal_rank_fusion(b)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first["one"], 1 / 61 + 1 / 62)
        self.assertEqual(len(trace["one"]), 2)

    def test_mixed_question_does_not_require_both_endpoints_in_one_record(self):
        question = "当时预测峰值、MAE和项目内部 Caution 是什么？"
        graph = build_question_graph(question)
        request = request_for_concept(question, graph, "internal_watch", "项目内部Watch")
        result = hybrid_search(request)
        self.assertIn("internal_watch", {hit.record["concept"] for hit in result.hits})

    def test_same_paragraph_deduplicates_but_keeps_other_concept_ids(self):
        result = hybrid_search(make_request("项目内部 Caution 是什么"))
        self.assertEqual(len(result.hits), len({candidate_key(hit) for hit in result.hits}))
        hit = result.hits[0]
        self.assertEqual(hit.record["concept"], "internal_watch")
        self.assertIn("internal_warning", {record["concept"] for record in hit.equivalent_records})
        answer = run_query("项目内部 Warning 是什么", audit_log=None)
        self.assertEqual(answer.knowledge[0].citation_id, "internal-warning-2026-09-30")

    def test_unknown_acronym_and_supplementary_only_are_not_grounded_answers(self):
        with self.assertRaises(NoEvidence):
            hybrid_search(make_request("你好", supplementary="项目内部Watch"))
        result = run_query("House Mill专属 SOP 是什么", audit_log=None)
        self.assertEqual(result.knowledge, [])
        self.assertEqual(result.evidence_bundle, [])
        self.assertIsNone(result.answer_text)

    def test_multiple_fragments_participate_in_agent_retrieval(self):
        question = "旧传感器怎么布置？它和FloodPred预测方法有什么关系？"
        result = run_investigation(question, audit_log=None)
        fragments = {q["text"] for run in result.retrieval_runs for q in run["queries"]
                     if q["role"] in ("fragment", "original+fragment")}
        self.assertIn("旧传感器怎么布置", fragments)
        self.assertTrue(any("有什么关系" in fragment for fragment in fragments))
        self.assertTrue(all(any(q["text"] == question for q in run["queries"])
                            for run in result.retrieval_runs))

    def test_document_batches_exclude_pure_numeric_and_negated_nodes(self):
        self.assertEqual(document_requests(build_question_graph("预测峰值和MAE的联系是什么")), ())
        requests = document_requests(build_question_graph("不要查旧传感器，只查论文预测方法"))
        self.assertTrue(all("不要查旧传感器" != request.fragment for request in requests))
        self.assertLessEqual(len(requests), 6)

    def test_new_unmapped_request_produces_candidates_without_answer(self):
        result = run_investigation("FloodPred以前测水的东西摆在哪里", audit_log=None)
        self.assertTrue(result.document_candidates)
        self.assertEqual(result.answer_status, "candidate_only")
        self.assertEqual(result.investigation_stop_reason, "candidates_only")
        self.assertFalse(result.knowledge)
        self.assertIsNone(result.answer_text)

    def test_local_excerpt_pdf_pages_and_hash_are_kept(self):
        result = hybrid_search(make_request("毕业论文的预测方法与输入数据"))
        hit = next(hit for hit in result.hits if hit.record["concept"] == "thesis_forecast_design")
        self.assertIn("PDF文件页 3", hit.locator)
        self.assertIn("Seven days of observations", hit.excerpt)
        self.assertEqual(len(hit.record["sha256"]), 64)
        self.assertEqual(len(hit.record["origin_sha256"]), 64)
        self.assertTrue(hit.ranking_trace)
        # Relocation must not skip an original PDF that is present two levels up.
        with TemporaryDirectory() as directory:
            migrated = Path(directory) / "CASA0016" / "FloodPred-Agent"
            original = Path(directory) / "Dissertation" / hit.record["origin_filename"]
            original.parent.mkdir()
            original.write_bytes(b"changed-pdf-only-in-test-tempdir")
            with patch("app.knowledge.ROOT", migrated):
                with self.assertRaises(SourceIntegrityError):
                    hybrid_search(make_request("毕业论文的预测方法与输入数据"), root=ROOT)

    def test_hash_failure_is_quarantined_and_all_failed_sources_abstain(self):
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        bad = {**data["records"][0], "id": "corrupt-copy", "concept": "corrupt-copy", "sha256": "0" * 64}
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"records": [*data["records"], bad]}), encoding="utf-8")
            result = hybrid_search(make_request("项目内部 Watch 是什么"), catalog=catalog, root=ROOT)
            self.assertEqual(result.quarantined[0]["id"], "corrupt-copy")
            self.assertNotIn("corrupt-copy", {hit.record["id"] for hit in result.hits})
            catalog.write_text(json.dumps({"records": [bad]}), encoding="utf-8")
            with self.assertRaises(SourceIntegrityError):
                hybrid_search(make_request("项目内部Watch"), catalog=catalog, root=ROOT)

    def test_conflicting_versions_are_not_silently_fused(self):
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        first = data["records"][0]
        second = {**first, "id": "future-watch", "version": "future-v2"}
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"records": [first, second]}), encoding="utf-8")
            with self.assertRaises(VersionConflict):
                hybrid_search(make_request("项目内部Watch"), catalog=catalog, root=ROOT)
            result = hybrid_search(make_request("项目内部Watch"), catalog=catalog, root=ROOT,
                                   version=first["version"])
            self.assertEqual(len(result.hits), 1)

    def test_document_checked_date_is_not_replay_as_of(self):
        result = run_query("项目内部 Caution 是什么", as_of_utc="2026-08-03T01:48:00Z", audit_log=None)
        self.assertTrue(result.knowledge)
        self.assertIsNone(result.retrieval_runs[0]["filters"]["checked_on_or_before"])
        self.assertIn("没有证明资料在回放时刻已可用", result.retrieval_runs[0]["time_note"])

    def test_persistent_trace_contains_hashes_not_queries(self):
        result = hybrid_search(make_request("旧水位设备的安装点在哪里"))
        trace = json.dumps(safe_trace(result), ensure_ascii=False)
        self.assertNotIn(result.request.original, trace)
        self.assertIn("query_hashes", trace)
        with TemporaryDirectory() as directory:
            log = Path(directory) / "requests.jsonl"
            run_investigation("旧水位设备的安装点在哪里", audit_log=log)
            content = log.read_text(encoding="utf-8")
            self.assertNotIn("旧水位设备的安装点在哪里", content)
            self.assertNotIn("布置 位置", content)
            self.assertIn("hybrid_retrieval", content)

    def test_thesis_source_request_does_not_abort_at_internal_watch(self):
        result = run_query("毕业论文中的内部 Caution 是什么", audit_log=None)
        self.assertEqual([item.source_type for item in result.knowledge], ["thesis"])
        self.assertFalse(result.errors)


if __name__ == "__main__":
    unittest.main()
