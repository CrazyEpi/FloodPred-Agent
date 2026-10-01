"""Small D4 retrieval checks: grounding, no-hit, and version conflict."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.knowledge import (
    DEFAULT_CATALOG,
    NoEvidence,
    SourceIntegrityError,
    VersionConflict,
    _semantic_rank,
    render_hits,
    search,
)


class KnowledgeTests(unittest.TestCase):
    def test_internal_watch_is_grounded_and_not_official_alert(self) -> None:
        hits = search("项目内部 Watch 是什么")
        self.assertEqual(len(hits), 1)
        shown = render_hits(hits)
        self.assertTrue("README.md:13-20 § 风险等级" in shown or "internal_watch.md:1-8 § 风险等级" in shown)
        self.assertIn("不是 Environment Agency 的官方 Flood Alert", shown)
        self.assertEqual(hits[0].record["source_type"], "internal_project")

    def test_missing_evidence_and_metadata_filter_fail_closed(self) -> None:
        with self.assertRaises(NoEvidence):
            search("House Mill 专属 SOP 是什么")
        with self.assertRaises(NoEvidence):
            search("项目内部 Watch 是什么", source_type="official_public_guidance")

    def test_conflicting_active_versions_are_not_silently_chosen(self) -> None:
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        conflicting = dict(data["records"][0])
        conflicting["id"] = "internal-watch-future-version"
        conflicting["version"] = "source-snapshot-future"
        conflicting["summary"] = "Conflicting threshold; must not surface automatically."
        data["records"].append(conflicting)
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "conflict.json"
            catalog.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(VersionConflict):
                search("项目内部 Watch 是什么", catalog=catalog)
            # Explicit version selection is permitted and remains traceable.
            hits = search("项目内部 Watch 是什么", catalog=catalog, version="source-snapshot-2026-09-29")
            self.assertEqual(len(hits), 1)

    def test_changed_local_source_hash_is_rejected(self) -> None:
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        data["records"] = [data["records"][0]]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / data["records"][0]["source_ref"]
            source.parent.mkdir(parents=True)
            source.write_text("# Changed source\n", encoding="utf-8")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SourceIntegrityError):
                search("项目内部 Watch 是什么", catalog=catalog, root=root)

    def test_hybrid_semantic_paraphrase_remains_only_a_candidate(self) -> None:
        with self.assertRaises(NoEvidence):
            search("旧水位设备的安装点在哪里")
        hits = search("旧水位设备的安装点在哪里", semantic=True)
        self.assertEqual(hits[0].record["concept"], "housemill_old_sensor")
        self.assertIn("semantic_candidate", hits[0].retrieval_methods)
        self.assertIn("相似度仅用于发现候选，不证明答案受到支持", render_hits(hits))
        with self.assertRaises(NoEvidence):
            search("House Mill 专属 SOP 是什么", semantic=True)
        with self.assertRaises(NoEvidence):
            search("完全没有线索的问题", semantic=True)

    def test_type_date_version_filters_precede_ranking(self) -> None:
        hits = search("项目内部 Watch 是什么", source_type="internal_project",
                      checked_on_or_before="2026-09-29", semantic=True)
        self.assertEqual([hit.record["id"] for hit in hits if hit.record["concept"] == "internal_watch"],
                         ["internal-watch-2026-09-29"])
        self.assertTrue(all(hit.record["source_type"] == "internal_project" for hit in hits))
        with self.assertRaises(NoEvidence):
            search("项目内部 Watch 是什么", source_type="internal_project",
                   checked_on_or_before="2026-09-28", semantic=True)
        with self.assertRaises(NoEvidence):
            search("Wilson 研究发现", source_type="research_paper",
                   checked_on_or_before="2026-10-02", semantic=True)
        with self.assertRaises(ValueError):
            search("项目内部 Watch", checked_on_or_before="10/02/2026")

    def test_semantic_hit_still_requires_unchanged_source_hash(self) -> None:
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        data["records"] = [data["records"][0]]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / data["records"][0]["source_ref"]
            source.parent.mkdir(parents=True)
            source.write_text("# mismatched source\n", encoding="utf-8")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SourceIntegrityError):
                search("项目内部 Watch", semantic=True, catalog=catalog, root=root)

    def test_large_catalog_uses_bounded_sparse_semantic_path(self) -> None:
        records = [{"id": f"record-{index}", "title": "传感器资料", "section": "布置",
                    "summary": f"水位设备布置记录 {index}", "keywords": ["旧传感器"]}
                   for index in range(260)]
        scores = _semantic_rank("旧水位设备的安装点", records)
        self.assertEqual(len(scores), 260)
        self.assertTrue(all(0 <= score <= 1 for score in scores.values()))


if __name__ == "__main__":
    unittest.main()
