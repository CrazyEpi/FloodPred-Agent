"""D4: source-aware keyword retrieval over a small, curated document catalog.

This is retrieval only. Catalog entries are evidence metadata, never instructions.
No LLM, embedding model, web request or site-specific SOP is used at query time.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "knowledge" / "catalog.json"
SOURCE_TYPES = frozenset({"internal_project", "evaluation_report", "official_public_guidance"})


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
    return f"{path}:{start}-{end} § {record['section']}", excerpt


def _load(catalog: Path) -> list[dict[str, Any]]:
    data = json.loads(catalog.read_text(encoding="utf-8"))
    records = data["records"]
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
