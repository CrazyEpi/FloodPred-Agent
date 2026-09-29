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
    render_hits,
    search,
)


class KnowledgeTests(unittest.TestCase):
    def test_internal_watch_is_grounded_and_not_official_alert(self) -> None:
        hits = search("项目内部 Watch 是什么")
        self.assertEqual(len(hits), 1)
        shown = render_hits(hits)
        self.assertIn("README.md:13-20 § 风险等级", shown)
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


if __name__ == "__main__":
    unittest.main()
