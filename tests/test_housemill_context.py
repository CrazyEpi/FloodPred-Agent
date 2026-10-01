"""Source-grounded House Mill background, without network or LLM calls."""

from __future__ import annotations

import unittest

from app.knowledge import DEFAULT_CATALOG, ROOT, NoEvidence, SourceIntegrityError, _load, _source_evidence, search
from app.rag import IntentPlan
from app.router import UnsupportedRoute, run_query


class HouseMillContextTests(unittest.TestCase):
    def ask(self, question: str):
        result = run_query(question, use_llm=False, audit_log=None)
        self.assertEqual(result.errors, [])
        return result

    def test_heritage_and_flood_context_are_separate_sources(self):
        result = self.ask("House Mill 是什么？为什么会受潮汐影响？")
        self.assertEqual(result.plan.knowledge_concepts, ("housemill_heritage", "housemill_flood_context"))
        self.assertEqual({item.source_type for item in result.knowledge}, {"heritage_public", "research_paper"})
        self.assertTrue(any("PDF文件页 1, 2" in item.locator for item in result.knowledge))

    def test_old_sensor_and_paper_counts_do_not_become_live_claims(self):
        result = self.ask("Duncan Wilson 的旧传感器怎么布置？论文中的 136 次与 42 次有什么区别？")
        self.assertEqual(result.plan.knowledge_concepts, ("housemill_old_sensor", "housemill_study_findings"))
        facts = " ".join(item.fact for item in result.knowledge)
        self.assertIn("不同阈值", facts)
        self.assertIn("不证明设备今天仍在运行", facts)
        self.assertIn("PDF文件页 3, 4", result.knowledge[1].locator)

    def test_old_project_connection_and_volunteer_need(self):
        connection = self.ask("旧 House Mill 监测和 FloodPred 是什么关系？")
        self.assertEqual(connection.plan.knowledge_concepts, ("housemill_project_connection",))
        self.assertEqual(connection.knowledge[0].source_type, "thesis")
        need = self.ask("House Mill 的志愿者需要什么信息？")
        self.assertEqual(need.plan.knowledge_concepts, ("housemill_volunteer_need",))
        self.assertIn("未提供 House Mill 现场应急 SOP", need.knowledge[0].fact)

    def test_curated_note_hash_is_checked(self):
        record = next(item for item in _load(DEFAULT_CATALOG) if item["concept"] == "housemill_old_sensor")
        changed = {**record, "sha256": "0" * 64}
        with self.assertRaises(SourceIntegrityError):
            _source_evidence(changed, ROOT)

    def test_colloquial_placement_questions_resolve_without_project_name(self):
        for question in ("旧传感器是怎么布置的", "之前那个水位仪怎么装的", "Duncan 的设备放哪儿？", "声纳探头安装在哪里？", "旧传感噐怎摸布置？"):
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertEqual([item.citation_id for item in result.knowledge], ["housemill-old-monitoring-github-20260930"])
                self.assertIn("地板开口下方的水道", result.knowledge[0].fact)

    def test_local_evidence_route_survives_bad_chat_classification(self):
        class MistakenPlanner:
            def plan(self, question, allowed_concepts):
                return IntentPlan("general")

            def synthesize(self, question, evidence):
                return "旧项目文档记录了传感器的历史布置；不能据此判断目前是否还在运行。"

        result = run_query("旧传感器是怎么布置的", use_llm=True, client=MistakenPlanner(), audit_log=None)
        self.assertEqual(result.response_mode, "project")
        self.assertEqual(result.plan.knowledge_concepts, ("housemill_old_sensor",))
        self.assertEqual(len(result.knowledge), 1)

    def test_typed_fuzzy_search_handles_small_typo_but_not_unrelated_question(self):
        hits = search("旧传感器怎摸布置", source_type="prior_project")
        self.assertEqual([hit.record["concept"] for hit in hits], ["housemill_old_sensor"])
        with self.assertRaises(NoEvidence):
            search("天气怎么样", source_type="prior_project")

    def test_current_equipment_status_is_not_inferred_from_old_repo(self):
        with self.assertRaisesRegex(UnsupportedRoute, "不能确认设备现在"):
            run_query("旧传感器现在安装在哪里？", use_llm=False, audit_log=None)


if __name__ == "__main__":
    unittest.main()
