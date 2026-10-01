"""Source-aware hybrid retrieval over a curated document catalog.

This is retrieval only. Catalog entries are evidence metadata, never instructions.
Lexical and lightweight local latent-semantic scores nominate candidates; neither
score establishes that a passage supports the user's answer. No web call is made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

import numpy as np


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
    retrieval_methods: tuple[str, ...] = ()
    semantic_similarity: float | None = None


_SEMANTIC_ALIASES = {
    "布点": "布置 传感器 位置",
    "安装点": "布置 传感器 位置",
    "测水设备": "传感器 声纳 水位仪",
    "平均偏差": "平均绝对误差 MAE",
    "洪灾提醒": "洪水警报 Flood Alert",
    "潮水": "潮汐 河流",
}
_DOMAIN_CUES = ("水位", "预测", "洪水", "洪灾", "预警", "提醒", "传感器", "设备", "布点", "安装点",
                "偏差", "误差", "评估", "潮汐", "潮水", "磨坊", "house mill", "mae", "sonar", "patchtst")


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


def _snapshot_date(record: dict[str, Any]) -> date | None:
    """A retrieval/verification date, not the document publication date."""
    value = record.get("checked_on")
    if value is None:
        match = re.search(r"\d{4}-\d{2}-\d{2}", record["version"])
        value = match.group() if match else None
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SourceIntegrityError(f"Invalid evidence snapshot date: {record['id']}") from exc


def _tokens(value: str) -> list[str]:
    tokens = []
    for part in re.findall(r"[a-z]+[\d.]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]+", value.casefold()):
        if '\u4e00' <= part[0] <= '\u9fff':
            tokens.extend(part[i:i + 2] for i in range(max(0, len(part) - 1)))
            if len(part) <= 3:
                tokens.append(part)
        else:
            tokens.append(part)
    return tokens


def _semantic_rank(question: str, records: list[dict[str, Any]]) -> dict[str, float]:
    """Local TF-IDF/LSA candidate ranking; no pretrained language-model claim."""
    if not records:
        return {}
    expanded = question + " " + " ".join(
        replacement for alias, replacement in _SEMANTIC_ALIASES.items() if alias in question
    )
    texts = [expanded] + [" ".join((item["title"], item["section"], item["summary"], *item["keywords"])) for item in records]
    tokenized = [set(_tokens(text)) for text in texts]
    vocabulary = sorted(set().union(*tokenized))
    if not vocabulary:
        return {}
    if len(records) > 256 or len(vocabulary) > 4096:
        # Bound memory/latency for a growing catalog. This branch retains
        # domain-expanded TF-IDF vectors but omits the expensive dense SVD.
        frequency = Counter(word for words in tokenized for word in words)
        weight = {word: (math.log((len(texts) + 1) / (count + 1)) + 1) ** 2
                  for word, count in frequency.items()}
        query_terms = tokenized[0]
        query_norm = math.sqrt(sum(weight[word] for word in query_terms))
        scores = {}
        for record, terms in zip(records, tokenized[1:]):
            denominator = query_norm * math.sqrt(sum(weight[word] for word in terms))
            scores[record["id"]] = sum(weight[word] for word in query_terms & terms) / denominator if denominator else 0.0
        return scores
    indices = {word: index for index, word in enumerate(vocabulary)}
    matrix = np.zeros((len(texts), len(vocabulary)), dtype=np.float64)
    for row, words in enumerate(tokenized):
        for word in words:
            matrix[row, indices[word]] = 1.0
    document_frequency = np.count_nonzero(matrix, axis=0)
    matrix *= np.log((len(texts) + 1) / (document_frequency + 1)) + 1
    if not np.any(matrix[0]):
        return {}
    # SVD learns co-occurrence only from the local corpus; a second lexical
    # component prevents latent-space artefacts from being treated as a match.
    lexical = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    lexical_scores = lexical[1:] @ lexical[0]
    if len(records) < 3:
        scores = lexical_scores
    else:
        _, _, vt = np.linalg.svd(matrix[1:], full_matrices=False)
        dimensions = min(8, len(records) - 1, vt.shape[0])
        projected = matrix @ vt[:dimensions].T
        projected /= np.maximum(np.linalg.norm(projected, axis=1, keepdims=True), 1e-12)
        latent_scores = projected[1:] @ projected[0]
        scores = 0.7 * lexical_scores + 0.3 * np.maximum(0.0, latent_scores)
    return {item["id"]: float(max(0.0, min(1.0, score))) for item, score in zip(records, scores)}


def _candidate_anchor(question: str, record: dict[str, Any]) -> bool:
    """Reject unsupported acronyms even if nearby words have vector overlap."""
    source_text = " ".join((record["title"], record["section"], record["summary"], *record["keywords"])).casefold()
    return all(word.casefold() in source_text for word in re.findall(r"[A-Za-z]{3,}", question))


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
    checked_on_or_before: str | None = None,
    semantic: bool = False,
    catalog: Path = DEFAULT_CATALOG,
    root: Path = ROOT,
) -> list[Hit]:
    """Return grounded hits, fail closed on missing evidence/version conflict."""
    if source_type is not None and source_type not in SOURCE_TYPES:
        raise ValueError(f"Unknown source type: {source_type}")
    try:
        cutoff = date.fromisoformat(checked_on_or_before) if checked_on_or_before else None
    except ValueError as exc:
        raise ValueError("checked_on_or_before must be YYYY-MM-DD") from exc
    expanded_question = question + " " + " ".join(
        replacement for alias, replacement in _SEMANTIC_ALIASES.items() if semantic and alias in question
    )
    q = _normal(expanded_question)
    if not q:
        raise ValueError("Question must not be empty")
    # In semantic mode, only an explicit type is a hard filter. A heuristic
    # inferred type could discard the right candidate before vector ranking.
    effective_type = source_type or (None if semantic else _inferred_type(question))
    eligible = []
    candidates = []
    for record in _load(catalog):
        if record["status"] != "active":
            continue
        if effective_type and record["source_type"] != effective_type:
            continue
        if version and record["version"] != version:
            continue
        if cutoff is not None:
            snapshot = _snapshot_date(record)
            if snapshot is None or snapshot > cutoff:
                continue
        eligible.append(record)
        keys = [_normal(word) for word in record["keywords"]]
        score = max((len(key) for key in keys if key and (key in q or q in key)), default=0)
        if score:
            candidates.append((record, score))
    semantic_scores = _semantic_rank(question, eligible) if semantic and any(cue in question.casefold() for cue in _DOMAIN_CUES) else {}
    if semantic_scores:
        candidate_ids = {record["id"] for record, _ in candidates}
        semantic_ids = {item_id for item_id, score in sorted(semantic_scores.items(), key=lambda item: -item[1])[:5]
                        if score >= 0.30}
        semantic_ids = {record["id"] for record in eligible if record["id"] in semantic_ids and _candidate_anchor(question, record)}
        candidates.extend((record, 0) for record in eligible if record["id"] in semantic_ids and record["id"] not in candidate_ids)
    # Only a typed, bounded fallback: a near-spelling must still resolve to a
    # curated record, whose file and version are verified below. Broad questions
    # without a source type do not use approximate matching.
    if not candidates and source_type is not None and len(q) <= 80:
        for record in eligible:
            similarity = max((_near_keyword(q, _normal(word)) for word in record["keywords"]), default=0.0)
            if similarity >= 0.84:
                candidates.append((record, 0))
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
        similarity = semantic_scores.get(record["id"])
        methods = (("keyword",) if score else ()) + (("semantic_candidate",) if similarity is not None and similarity >= 0.30 and _candidate_anchor(question, record) else ())
        if not methods:
            methods = ("fuzzy_keyword",)
        hits.append(Hit(record, score, locator, excerpt, methods, similarity))
    return sorted(hits, key=lambda hit: (-bool(hit.score), -hit.score, -(hit.semantic_similarity or 0), hit.record["id"]))


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
                    f"检索方式：{', '.join(hit.retrieval_methods)}；相似度仅用于发现候选，不证明答案受到支持。",
                    *( [f"原文片段：\n{hit.excerpt}"] if hit.excerpt else [] ),
                ]
            )
        )
    return "\n\n".join(parts)
