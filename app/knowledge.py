"""D4: source-aware keyword retrieval over a small, curated document catalog.

This is retrieval only. Catalog entries are evidence metadata, never instructions.
No LLM, embedding model, web request or site-specific SOP is used at query time.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
_SOURCE_CATALOG = ROOT / "knowledge" / "catalog.json"
_DEMO_CATALOG = ROOT / "demo_data" / "knowledge" / "catalog.json"
_DATA_MODE = os.environ.get("FLOODPRED_DATA_MODE", "")
DEFAULT_CATALOG = _DEMO_CATALOG if _DATA_MODE == "demo" or not (ROOT / "backup" / "source_snapshot_2026-09-29").is_dir() else _SOURCE_CATALOG
THESIS_CATALOG = ROOT / "knowledge" / "thesis_catalog.json"
RULES_CATALOG = ROOT / "knowledge" / "rules_catalog.json"
HOUSEMILL_CATALOG = ROOT / "knowledge" / "housemill_catalog.json"
SOURCE_TYPES = frozenset({"internal_project", "evaluation_report", "official_public_guidance", "thesis", "research_paper", "prior_project", "heritage_public"})


class KnowledgeError(Exception):
    pass


class NoEvidence(KnowledgeError):
    pass


class VersionConflict(KnowledgeError):
    pass


class SourceIntegrityError(KnowledgeError):
    pass


@dataclass(frozen=True)
class Hit:
    record: dict[str, Any]
    score: int
    locator: str
    excerpt: str | None


def _normal(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value.casefold(), flags=re.UNICODE)


def _near_keyword(question: str, keyword: str) -> float:
    """Typo-tolerant fallback over short aliases, never a substitute for source checks."""
    if len(question) < 5 or len(keyword) < 5:
        return 0.0
    if len(question) <= len(keyword):
        return SequenceMatcher(None, question, keyword).ratio()
    length = len(keyword)
    return max(SequenceMatcher(None, question[index:index + length], keyword).ratio()
               for index in range(len(question) - length + 1))


def _source_evidence(record: dict[str, Any], root: Path) -> tuple[str, str | None]:
    ref = record["source_ref"]
    if record["source_type"] == "official_public_guidance":
        if not ref.startswith("https://www.gov.uk/"):
            raise SourceIntegrityError(f"Unapproved official URL: {ref}")
        return f"{ref} § {record['section']}", None

    path = (root / ref).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise SourceIntegrityError(f"Source path escapes project: {ref}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise SourceIntegrityError(f"Source hash changed: {ref}")
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end = record["line_start"], record["line_end"]
    if not 1 <= start <= end <= len(lines):
        raise SourceIntegrityError(f"Invalid line span: {ref}:{start}-{end}")
    excerpt = "\n".join(lines[start - 1:end])
    if record["source_type"] == "thesis":
        origin = ROOT.parent / "Dissertation" / record["origin_filename"]
        if origin.is_file() and hashlib.sha256(origin.read_bytes()).hexdigest() != record["origin_sha256"]:
            raise SourceIntegrityError("Source dissertation PDF hash changed; thesis excerpt refused")
        pages = ", ".join(str(page) for page in record["pdf_pages"])
        return f"{record['origin_filename']} PDF文件页 {pages} | 摘录 {path}:{start}-{end} § {record['section']}", excerpt
    if record["source_type"] == "research_paper":
        origin = Path.home() / "Downloads" / record["origin_filename"]
        if origin.is_file() and hashlib.sha256(origin.read_bytes()).hexdigest() != record["origin_sha256"]:
            raise SourceIntegrityError("Source research PDF hash changed; curated note refused")
        pages = ", ".join(str(page) for page in record["pdf_pages"])
        return f"{record['origin_filename']} PDF文件页 {pages} | 整理笔记 {path}:{start}-{end} § {record['section']}", excerpt
    if record["source_type"] in {"prior_project", "heritage_public"}:
        url = record["origin_url"]
        if not (url.startswith("https://github.com/djdunc/housemill") or url == "https://historicengland.org.uk/listing/the-list/list-entry/1080970"):
            raise SourceIntegrityError(f"Unapproved background source URL: {url}")
        return f"{url} | 整理笔记 {path}:{start}-{end} § {record['section']}", excerpt
    return f"{path}:{start}-{end} § {record['section']}", excerpt


def _load(catalog: Path) -> list[dict[str, Any]]:
    data = json.loads(catalog.read_text(encoding="utf-8"))
    records = list(data["records"])
    if catalog.resolve() == DEFAULT_CATALOG.resolve():
        rules = json.loads(RULES_CATALOG.read_text(encoding="utf-8"))["records"]
        if DEFAULT_CATALOG == _DEMO_CATALOG:
            watch = next(item for item in records if item["concept"] == "internal_watch")
            rules = [{**item, **{field: watch[field] for field in ("source_ref", "line_start", "line_end", "sha256")}} for item in rules]
        records.extend(rules)
        thesis_data = json.loads(THESIS_CATALOG.read_text(encoding="utf-8"))
        records.extend({**item, "origin_filename": thesis_data["origin_filename"], "origin_sha256": thesis_data["origin_sha256"]} for item in thesis_data["records"])
        housemill_data = json.loads(HOUSEMILL_CATALOG.read_text(encoding="utf-8"))
        for item in housemill_data["records"]:
            if item["source_type"] == "research_paper":
                records.append({**item, "origin_filename": housemill_data["paper_filename"], "origin_sha256": housemill_data["paper_sha256"]})
            elif item["source_type"] == "thesis":
                records.append({**item, "origin_filename": housemill_data["thesis_filename"], "origin_sha256": housemill_data["thesis_sha256"]})
            else:
                records.append(item)
    ids: set[str] = set()
    for item in records:
        if item["id"] in ids:
            raise SourceIntegrityError(f"Duplicate record id: {item['id']}")
        ids.add(item["id"])
        if item["source_type"] not in SOURCE_TYPES:
            raise SourceIntegrityError(f"Unknown source type: {item['source_type']}")
    return records


def _inferred_type(question: str) -> str | None:
    q = _normal(question)
    if any(token in q for token in ("wilson", "zhang", "cupum", "unchartedwaters")):
        return "research_paper"
    if any(token in q for token in ("论文", "毕业设计", "dissertation", "thesis")):
        return "thesis"
    if any(token in q for token in ("项目内部", "内部watch", "项目阈值")):
        return "internal_project"
    if any(token in q for token in ("官方", "floodalert", "environmentagency")):
        return "official_public_guidance"
    if any(token in q for token in ("评估", "mae", "召回", "模型限制")):
        return "evaluation_report"
    return None


def search(
    question: str,
    *,
    source_type: str | None = None,
    version: str | None = None,
    catalog: Path = DEFAULT_CATALOG,
    root: Path = ROOT,
) -> list[Hit]:
    """Return grounded hits, fail closed on missing evidence/version conflict."""
    if source_type is not None and source_type not in SOURCE_TYPES:
        raise ValueError(f"Unknown source type: {source_type}")
    q = _normal(question)
    if not q:
        raise ValueError("Question must not be empty")
    effective_type = source_type or _inferred_type(question)
    eligible = []
    candidates = []
    for record in _load(catalog):
        if record["status"] != "active":
            continue
        if effective_type and record["source_type"] != effective_type:
            continue
        if version and record["version"] != version:
            continue
        eligible.append(record)
        keys = [_normal(word) for word in record["keywords"]]
        score = max((len(key) for key in keys if key and (key in q or q in key)), default=0)
        if score:
            candidates.append((record, score))
    # Only a typed, bounded fallback: a near-spelling must still resolve to a
    # curated record, whose file and version are verified below. Broad questions
    # without a source type do not use approximate matching.
    if not candidates and source_type is not None and len(q) <= 80:
        for record in eligible:
            similarity = max((_near_keyword(q, _normal(word)) for word in record["keywords"]), default=0.0)
            if similarity >= 0.84:
                candidates.append((record, round(similarity * 100)))
    if not candidates:
        raise NoEvidence("未找到匹配的已核对来源；不生成猜测答案。")

    # Check all active records for a matched concept, not just records whose
    # keyword aliases happen to match this wording of the question.
    matched_concepts = {
        (record["source_type"], record["concept"]) for record, _ in candidates
    }
    by_concept: dict[tuple[str, str], set[str]] = {}
    for record in eligible:
        key = record["source_type"], record["concept"]
        if key in matched_concepts:
            by_concept.setdefault(key, set()).add(record["version"])
    conflicts = [key for key, versions in by_concept.items() if len(versions) > 1]
    if conflicts:
        raise VersionConflict(f"同一知识概念出现多个有效版本：{conflicts}；请先人工确认。")

    hits = []
    for record, score in candidates:
        locator, excerpt = _source_evidence(record, root)
        hits.append(Hit(record, score, locator, excerpt))
    return sorted(hits, key=lambda hit: (-hit.score, hit.record["id"]))


def render_hits(hits: list[Hit]) -> str:
    parts = []
    for hit in hits:
        record = hit.record
        parts.append(
            "\n".join(
                [
                    record["summary"],
                    f"来源类型：{record['source_type']}；版本：{record['version']}",
                    f"来源定位：{hit.locator}",
                    *( [f"原文片段：\n{hit.excerpt}"] if hit.excerpt else [] ),
                ]
            )
        )
    return "\n\n".join(parts)
